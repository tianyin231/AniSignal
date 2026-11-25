"""兑换码双轨抽取、分游戏前瞻失效推断、多源交叉确认与保鲜期治理。

设计原则：
1. 官方内容与社区强信号帖（B 站前瞻汇总/攻略 UP 主）双轨入库，统一通过 upsert 合并：
   - 按独立来源（Source.id）累计 source_count，避免同一博主复读刷高确认数；
   - 后续任意帖补出更准确的 expires_at、server、reward 时自动回填，官方来源自动升级 source_kind；
   - Bot 即时推送门禁：官方码立即推；社区码仅当 >=2 位独立来源交叉确认后才推（防单博主假码炸群）。
2. 三层有效期治理（彻底消除无过期时间的僵尸码）：
   - 优先级 1：原文明确给出的截止时间，或同期（±48h）官方前瞻动态里公布的统一兑换码截止日程；
   - 优先级 2：前瞻/直播码未注明具体时间时，锚定官方前瞻 broadcast 开播日（无日程则用发布日），按各游戏常见规律自动推算并标 expiry_estimated=True：
     * 原神（genshin）：次日中午 12:00（北京时间）
     * 崩铁（hsr）及默认：次日 23:59（北京时间）
     * 绝区零（zzz）/ 鸣潮（wuwa）：第 2 日 23:59（北京时间）
   - 优先级 3：非前瞻且无明确有效期的码，展示保鲜期上限 14 天（超时自动隐藏）。
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.communities import feed_community_keys, get_community
from app.models import ContentSchedule, ContentVersion, RawContent, RedeemCode, Source
from app.services.ai import ai_available, chat, get_ai_config, record_usage, retry_allowed

logger = logging.getLogger(__name__)

# 无明确有效期普通兑换码的展示保鲜期上限（天）
UNKNOWN_EXPIRY_TTL_DAYS = 14
# 社区 UP 主兑换码扫库窗口（天）与单轮上限
COMMUNITY_CODE_WINDOW_DAYS = 7
COMMUNITY_CODE_BATCH_LIMIT = 8
# 社区码触发 Bot 推送所需的最少独立来源数
COMMUNITY_BOT_PUSH_MIN_SOURCES = 2
_TEXT_LIMIT = 6000

# 分游戏前瞻直播码默认失效规则：(锚定北京时间日期偏移天数, 北京时间小时, 分钟)
LIVESTREAM_EXPIRY_RULES: dict[str, tuple[int, int, int]] = {
    "genshin": (1, 12, 0),   # 原神：次日中午 12:00（北京时间）
    "hsr": (1, 23, 59),      # 崩坏：星穹铁道：次日 23:59（北京时间）
    "zzz": (2, 23, 59),      # 绝区零：第 2 日 23:59（北京时间）
    "wuwa": (2, 23, 59),     # 鸣潮：第 2 日 23:59（北京时间）
}
DEFAULT_LIVESTREAM_EXPIRY_RULE: tuple[int, int, int] = (1, 23, 59)

_ASCII_CODE_MIN = 6
_ASCII_CODE_MAX = 32
_CJK_CODE_MIN = 4
_CJK_CODE_MAX = 24

_NOISE_ASCII_CODES = frozenset({
    "bilibili", "youtube", "twitter", "hoyolab", "mihoyo", "hoyoverse", "kurogames",
    "steam", "taptap", "discord", "twitch", "facebook", "instagram", "reddit",
    "wutheringwaves", "genshinimpact", "honkaistarrail", "zenlesszonezero",
    "arknights", "infinitynikki", "endfield", "redemption", "redeemcode",
    "specialprogram", "livestream", "download", "official", "https",
})
_NOISE_CJK_SUBSTRINGS = (
    "兑换码", "礼包码", "口令码", "福利码", "过期时间", "截止时间", "有效期",
    "评论区", "点击复制", "前瞻直播", "特别节目", "置顶评论",
)
_BILI_ID_RE = re.compile(r"^(?:BV1[0-9A-Za-z]{9}|av\d+)$", re.IGNORECASE)
_URL_EXT_RE = re.compile(r"(?:https?|www\.|\.(?:com|cn|net|org|gg|tv|link|io|xyz|jp|html?))", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{4,30}[A-Za-z0-9]$")
_CJK_CODE_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9._-]+$")
_CODE_SIGNAL_RE = re.compile(
    r"兑换码|礼包码|口令码|福利码|前瞻码|redeem\s*codes?|redemption\s*codes?|gift\s*codes?|promo\s*codes?",
    re.IGNORECASE,
)
_NON_GAME_CODE_RE = re.compile(r"(?:API|额度|算力|会员|课程|优惠券|邀请)\s*兑换码|DeepSeek.*兑换码", re.IGNORECASE)
_LIVESTREAM_CONTEXT_RE = re.compile(
    r"前瞻|特别节目|直播(?:兑换|礼包|福利)?码|Special\s+Program|Livestream",
    re.IGNORECASE,
)
_CODE_DEADLINE_TITLE_RE = re.compile(r"兑换码|礼包码|口令码|redemption|redeem", re.IGNORECASE)
_STRIP_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_CANDIDATE_ASCII_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9/])([A-Za-z0-9][A-Za-z0-9._-]{5,31})(?![A-Za-z0-9])")
_CANDIDATE_CJK_CODE_RE = re.compile(
    r"(?:兑换码|礼包码|口令码|福利码|国服|国际服|亚服|欧服|美服|钻|抽|星声|原石|星琼|菲林)[^\n。！？；]{0,14}[:：🎁「『]\s*([\u4e00-\u9fffA-Za-z0-9._-]{4,24})"
)

_COMMUNITY_SYSTEM = (
    "你是二次元游戏兑换码结构化抽取助手。严格按要求输出 JSON，"
    "只抽取正文里真实存在的游戏内兑换码/口令码，绝不编造、绝不抽取视频号/QQ群号/网址/角色名。"
)

_COMMUNITY_USER_TEMPLATE = """从以下玩家/攻略博主发布的游戏内容中，仅抽取「游戏内兑换码 / 口令码」，输出 JSON：
{{"codes": [{{"code": "...", "expires_at": null, "is_livestream": false, "server": null, "reward": null, "evidence": "含该兑换码的原文片段"}}]}}

严格规则：
1. 只抽取正文里明确作为「游戏内兑换码/礼包码/口令码」给出的码。支持英文/数字组合（可含点号或连字符，如 INFINITYNIKKI3.0THISISART）及中文口令码（如 为暖暖攒下生日祝福、无限暖暖3.0马力全开）。
2. 严禁抽取：BV号、QQ群号、纯数字、网址、活动名、角色名、游戏名本身、抽奖口令、非游戏码（如 API 额度码）。如果正文只写了“兑换码见评论区/置顶/视频内”而没有给出具体码，必须输出空列表。
3. expires_at：若原文明确写出截止/过期时间，输出带时区的 ISO 格式（默认北京时间 +08:00，未写年份按 {year} 年，参考发布时间 {published_at}）；若原文未写具体时间，填 null。
4. is_livestream：仅当该兑换码明确属于版本前瞻直播 / 特别节目 / Special Program 发放的短效兑换码时为 true；生日码/公测常驻码/常规渠道码为 false。
5. server：原文明确区分区服时填 "cn"（国服）、"global"（国际服/亚服/美服/欧服）或 "all"（全服通用）；未区分填 null。
6. reward：如有简短奖励说明（如 "100星声"、"10抽"、"100钻"），填入不超过 40 字；否则填 null。
7. evidence：逐字摘录原文中包含该兑换码的短片段（10–80 字）。

所属游戏分区：{community_label}
标题：{title}
正文：{text}"""


def normalize_code(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    cleaned = unicodedata.normalize("NFKC", raw).strip()
    cleaned = cleaned.strip("「」『』\"'`【】[]()（）<>《》,，。.;；:：!！?？")
    return cleaned.strip()


def normalize_server(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    val = raw.strip().lower()
    if val in {"cn", "国服", "官服", "b服"}:
        return "cn"
    if val in {"global", "intl", "international", "国际服", "外服", "亚服", "日服", "美服", "欧服"}:
        return "global"
    if val in {"all", "全服", "通用"}:
        return "all"
    return None


def normalize_reward(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = re.sub(r"\s+", " ", raw).strip()[:100]
    return cleaned or None


def is_valid_redeem_code(code: str, source_text: str | None = None) -> bool:
    """校验兑换码格式（兼容英数/点号/连字符码与中文口令码），并排除 BV 号/纯数字/网址/幻觉。"""
    if not code or any(ch.isspace() for ch in code):
        return False
    lower = code.lower()
    if _BILI_ID_RE.match(code) or _URL_EXT_RE.search(code):
        return False

    has_cjk = bool(_CJK_RE.search(code))
    if has_cjk:
        if not (_CJK_CODE_MIN <= len(code) <= _CJK_CODE_MAX):
            return False
        if not _CJK_CODE_RE.fullmatch(code):
            return False
        if any(noise in code for noise in _NOISE_CJK_SUBSTRINGS):
            return False
    else:
        if not (_ASCII_CODE_MIN <= len(code) <= _ASCII_CODE_MAX):
            return False
        if not _ASCII_CODE_RE.fullmatch(code):
            return False
        # 拒绝纯数字（QQ 群号、日期、UID 等）
        if not re.search(r"[A-Za-z]", code):
            return False
        if lower in _NOISE_ASCII_CODES:
            return False

    if source_text is not None:
        norm_source = unicodedata.normalize("NFKC", source_text).lower()
        if lower not in norm_source:
            return False
    return True


def looks_like_livestream_context(title: str, text: str) -> bool:
    return bool(_LIVESTREAM_CONTEXT_RE.search(f"{title}\n{text}"))


def looks_like_code_post(title: str, text: str) -> bool:
    """社区非官方内容零成本前置过滤器：排除空简介、置顶引导帖与非游戏码，只放行正文里真有码的帖子。"""
    combined = f"{title}\n{text}"
    if not _CODE_SIGNAL_RE.search(combined):
        return False
    if _NON_GAME_CODE_RE.search(combined):
        return False
    body = (text or "").strip()
    if len(body) < 12 or body in {"-", "—", "无"}:
        return False

    # 检查正文中是否存在至少一个合理的中文口令码或英数候选码（去 URL 后匹配）
    stripped = _STRIP_URL_RE.sub(" ", unicodedata.normalize("NFKC", body))
    if _CANDIDATE_CJK_CODE_RE.search(stripped):
        return True
    for match in _CANDIDATE_ASCII_TOKEN_RE.finditer(stripped):
        token = match.group(1)
        if not re.search(r"[A-Z0-9]", token):
            continue
        if is_valid_redeem_code(token):
            return True
    return False


def active_redeem_code_conditions(now: datetime | None = None) -> list:
    """全站统一的「当前有效兑换码」过滤条件（API / Bot / MCP 共用）。

    - 有 expires_at：必须尚未过期（expires_at > now）；
    - 无 expires_at：必须在 14 天保鲜期内（按原内容发布时间优先，兜底 created_at），防止历史僵尸码常驻。
    """
    now = now or utcnow()
    fresh_cutoff = now - timedelta(days=UNKNOWN_EXPIRY_TTL_DAYS)
    return [
        RedeemCode.status == "active",
        RawContent.hidden.is_(False),
        or_(
            RedeemCode.expires_at > now,
            and_(
                RedeemCode.expires_at.is_(None),
                func.coalesce(RawContent.published_at, RedeemCode.created_at) >= fresh_cutoff,
            ),
        ),
    ]


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return to_naive_utc(dt)


def parse_community_codes(
    raw_out: str,
    *,
    source_text: str | None = None,
    strict: bool = False,
) -> list[dict]:
    """解析社区兑换码抽取 JSON。"""
    text = (raw_out or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if strict:
            raise ValueError("invalid community code JSON") from None
        return []
    if not isinstance(payload, dict) or (strict and not isinstance(payload.get("codes"), list)):
        if strict:
            raise ValueError("community code extraction must contain codes list")
        return []

    compact = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()
    compact_source = compact(source_text) if source_text is not None else None

    results: list[dict] = []
    seen: set[str] = set()
    for entry in payload.get("codes") or []:
        if not isinstance(entry, dict):
            continue
        code = normalize_code(entry.get("code"))
        if not is_valid_redeem_code(code, source_text=source_text):
            continue
        if compact_source is not None:
            evidence = str(entry.get("evidence") or "").strip()
            if evidence and compact(evidence) not in compact_source:
                # 只要码本身已在原文中，允许轻微摘录差异，但明显幻觉证据则跳过
                if compact(code) not in compact(evidence):
                    continue
        dedupe_key = code.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        results.append(
            {
                "code": code,
                "expires_at": _parse_dt(entry.get("expires_at")),
                "is_livestream": entry.get("is_livestream") is True,
                "server": normalize_server(entry.get("server")),
                "reward": normalize_reward(entry.get("reward")),
            }
        )
    return results


async def find_official_code_deadline(
    session: AsyncSession,
    community: str,
    anchor_at: datetime,
) -> datetime | None:
    """查找同期（±48h 内发布、未来 14 天内截止）官方发布的「兑换码截止」日程。"""
    rows = (
        await session.execute(
            select(ContentSchedule)
            .join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
            .join(Source, Source.id == RawContent.source_id)
            .where(
                ContentSchedule.community == community,
                ContentSchedule.kind == "deadline",
                Source.identity == "official",
                RawContent.hidden.is_(False),
                ContentSchedule.start_at >= anchor_at - timedelta(hours=12),
                ContentSchedule.start_at <= anchor_at + timedelta(days=14),
            )
            .order_by(ContentSchedule.start_at.asc())
        )
    ).scalars().all()
    for row in rows:
        if _CODE_DEADLINE_TITLE_RE.search(row.title or ""):
            return row.start_at
    return None


async def infer_livestream_expiry(
    session: AsyncSession,
    community: str,
    published_at: datetime,
) -> tuple[datetime, bool]:
    """为未注明具体失效时间的前瞻直播兑换码推断有效期。

    返回 `(expires_at_utc, is_estimated)`：
    1. 若同期已有官方发布的兑换码截止日程，直接采用官方精确时间（is_estimated=False）；
    2. 否则锚定同游戏 48h 内的官方前瞻 broadcast 日程（无则用发布时间），按分游戏北京时间规律计算（is_estimated=True）。
    """
    official_deadline = await find_official_code_deadline(session, community, published_at)
    if official_deadline is not None:
        return official_deadline, False

    broadcasts = (
        await session.execute(
            select(ContentSchedule.start_at)
            .join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
            .join(Source, Source.id == RawContent.source_id)
            .where(
                ContentSchedule.community == community,
                ContentSchedule.kind == "broadcast",
                Source.identity == "official",
                RawContent.hidden.is_(False),
                ContentSchedule.start_at >= published_at - timedelta(hours=48),
                ContentSchedule.start_at <= published_at + timedelta(hours=12),
            )
        )
    ).scalars().all()
    anchor = (
        min(broadcasts, key=lambda dt: abs((dt - published_at).total_seconds()))
        if broadcasts
        else published_at
    )

    days_offset, hour_bj, minute_bj = LIVESTREAM_EXPIRY_RULES.get(
        community, DEFAULT_LIVESTREAM_EXPIRY_RULE
    )
    anchor_bj = anchor + timedelta(hours=8)
    expiry_bj = anchor_bj.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=days_offset, hours=hour_bj, minutes=minute_bj
    )
    return expiry_bj - timedelta(hours=8), True


async def backfill_expiry_from_official_deadline(
    session: AsyncSession,
    community: str,
    deadline_at: datetime,
    published_at: datetime,
) -> int:
    """当官方前瞻动态公布了统一兑换码失效时间时，自动回填同期缺有效期或估算有效期的兑换码。"""
    window_start = published_at - timedelta(hours=48)
    window_end = published_at + timedelta(hours=48)
    rows = (
        await session.execute(
            select(RedeemCode)
            .join(RawContent, RawContent.id == RedeemCode.raw_content_id)
            .where(
                RedeemCode.community == community,
                RedeemCode.status == "active",
                or_(RedeemCode.expires_at.is_(None), RedeemCode.expiry_estimated.is_(True)),
                func.coalesce(RawContent.published_at, RedeemCode.created_at) >= window_start,
                func.coalesce(RawContent.published_at, RedeemCode.created_at) <= window_end,
            )
        )
    ).scalars().all()
    updated = 0
    for row in rows:
        row.expires_at = deadline_at
        row.expiry_estimated = False
        updated += 1
    return updated


async def upsert_redeem_code(
    session: AsyncSession,
    *,
    content_id: int,
    source_id: int | None,
    community: str,
    code: str,
    expires_at: datetime | None,
    expiry_estimated: bool = False,
    source_kind: str = "official",
    server: str | None = None,
    reward: str | None = None,
    now: datetime | None = None,
) -> tuple[RedeemCode, bool, bool]:
    """写入或合并同分区兑换码；返回 `(row, created, should_notify)`。

    - 同分区英数兑换码按大小写不敏感查重（避免 trainofsouls 与 TRAINOFSOULS 重复）；
    - 按独立 source_id 累计确认数，防止单博主连发多条视频虚增可信度；
    - 精确有效期优先覆盖空值或估算值；官方来源优先升级 community 来源；
    - should_notify：官方新码立即为 True；社区码在首次达到 2 位独立来源交叉确认或升级为官方时为 True。
    """
    now = now or utcnow()
    source_token = f"src:{source_id}" if source_id else f"rc:{content_id}"
    is_official = source_kind == "official"

    # 精确匹配优先，英数再做大小写不敏感匹配
    existing = await session.scalar(
        select(RedeemCode).where(RedeemCode.community == community, RedeemCode.code == code)
    )
    if existing is None and not _CJK_RE.search(code):
        existing = await session.scalar(
            select(RedeemCode).where(
                RedeemCode.community == community,
                func.lower(RedeemCode.code) == code.lower(),
            )
        )

    if existing is None:
        row = RedeemCode(
            raw_content_id=content_id,
            community=community,
            code=code,
            status="active",
            expires_at=expires_at,
            source_kind="official" if is_official else "community",
            source_count=1,
            server=server,
            reward=reward,
            expiry_estimated=bool(expiry_estimated and expires_at is not None),
            confirmed_sources=[source_token],
            created_at=now,
        )
        session.add(row)
        return row, True, is_official

    was_eligible = existing.source_kind == "official" or (existing.source_count or 1) >= COMMUNITY_BOT_PUSH_MIN_SOURCES

    sources = list(existing.confirmed_sources or [])
    if not sources:
        sources = [f"rc:{existing.raw_content_id}"]
    if source_token not in sources:
        sources.append(source_token)
        existing.confirmed_sources = sources
        existing.source_count = len(sources)

    if is_official and existing.source_kind != "official":
        existing.source_kind = "official"
        existing.raw_content_id = content_id

    if expires_at is not None:
        if existing.expires_at is None or (existing.expiry_estimated and not expiry_estimated):
            existing.expires_at = expires_at
            existing.expiry_estimated = expiry_estimated

    if server and not existing.server:
        existing.server = server
    if reward and not existing.reward:
        existing.reward = reward

    now_eligible = existing.source_kind == "official" or (existing.source_count or 1) >= COMMUNITY_BOT_PUSH_MIN_SOURCES
    should_notify = (not was_eligible) and now_eligible
    return existing, False, should_notify


async def extract_community_codes(
    session: AsyncSession,
    content_id: int,
    *,
    notify: bool = True,
) -> dict:
    """从单条社区 UP 主强信号帖中抽取兑换码（绝不抽取日程，不影响活动日历）。"""
    from app.services.extract import _push_to_targets

    pair = (
        await session.execute(
            select(RawContent, ContentVersion, Source.identity)
            .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
            .join(Source, RawContent.source_id == Source.id, isouter=True)
            .where(RawContent.id == content_id)
            .order_by(ContentVersion.version.desc())
            .limit(1)
        )
    ).first()
    if pair is None:
        return {"status": "skipped", "reason": "content_not_found"}

    raw, version, identity = pair
    if (identity or "unknown") == "official":
        return {"status": "skipped", "reason": "is_official"}
    meta = get_community(raw.community)
    if meta.domain != "game":
        raw.extracted_at = utcnow()
        await session.commit()
        return {"status": "skipped", "reason": "not_game"}

    title = (version.title if version else None) or ""
    text = ((version.text if version else None) or "")[:_TEXT_LIMIT]
    if not looks_like_code_post(title, text):
        raw.extracted_at = utcnow()
        await session.commit()
        return {"status": "skipped", "reason": "no_code_signal"}

    config = await get_ai_config(session)
    if not ai_available(config):
        return {"status": "skipped", "reason": "ai_not_configured"}

    published = raw.published_at or raw.collected_at
    source_published = raw.published_at.isoformat() if raw.published_at else None
    published_bj = published + timedelta(hours=8)
    prompt = _COMMUNITY_USER_TEMPLATE.format(
        year=published_bj.year,
        published_at=f"{published_bj:%Y/%m/%d %H:%M}（北京时间）",
        community_label=meta.label,
        title=title,
        text=text,
    )
    started = time.monotonic()
    usage = None
    try:
        raw_out, usage = await chat(config, _COMMUNITY_SYSTEM, prompt, json_mode=True)
        codes = parse_community_codes(raw_out, source_text=f"{title}\n{text}", strict=True)
    except Exception as exc:  # noqa: BLE001
        await record_usage(
            session,
            task="extract_community_codes",
            config=config,
            usage=usage,
            duration_ms=int((time.monotonic() - started) * 1000),
            ref_id=content_id,
            status="failed",
        )
        return {"status": "failed", "reason": str(exc)[:200]}

    await record_usage(
        session,
        task="extract_community_codes",
        config=config,
        usage=usage,
        duration_ms=int((time.monotonic() - started) * 1000),
        ref_id=content_id,
    )

    community = raw.community or "wuwa"
    now = utcnow()
    new_codes = 0
    updated_codes = 0
    for entry in codes:
        expires_at = entry["expires_at"]
        expiry_estimated = False
        if expires_at is None and entry["is_livestream"]:
            expires_at, expiry_estimated = await infer_livestream_expiry(session, community, published)

        row, created, should_notify = await upsert_redeem_code(
            session,
            content_id=content_id,
            source_id=raw.source_id,
            community=community,
            code=entry["code"],
            expires_at=expires_at,
            expiry_estimated=expiry_estimated,
            source_kind="community",
            server=entry["server"],
            reward=entry["reward"],
            now=now,
        )
        if created:
            new_codes += 1
        else:
            updated_codes += 1

        if notify and should_notify and (not row.expires_at or row.expires_at > now):
            source = {
                "id": content_id,
                "content_ids": [content_id],
                "title": title,
                "text": text,
                "url": raw.url,
                "community": community,
                "identity": "community",
                "published_at": source_published,
            }
            reward_line = f"\n奖励：{row.reward}" if row.reward else ""
            expiry_line = (
                f"\n{'预计' if row.expiry_estimated else ''}截止：{(row.expires_at + timedelta(hours=8)).strftime('%m/%d %H:%M')}（北京时间）"
                if row.expires_at
                else ""
            )
            pushed = await _push_to_targets(
                session,
                community,
                "critical",
                f"🎫 新兑换码（{row.source_count} 位博主确认）：{row.code}{reward_line}{expiry_line}\n来源：{title}",
                f"code:{community}:{row.code}",
                brief={
                    "mode": "notice",
                    "code": row.code,
                    "expires_at": min(row.expires_at or now + timedelta(hours=12), now + timedelta(hours=12)).isoformat(),
                    "items": [{**source, "sources": [source]}],
                },
            )
            logger.info("community redeem code %s (sources=%s) pushed to %s targets", row.code, row.source_count, pushed)

    raw.extracted_at = utcnow()
    await session.commit()
    return {"status": "ok", "codes": new_codes, "confirmed": updated_codes}


async def sweep_community_codes(
    session: AsyncSession,
    *,
    limit: int = COMMUNITY_CODE_BATCH_LIMIT,
    window_days: int = COMMUNITY_CODE_WINDOW_DAYS,
    notify: bool = True,
) -> dict:
    """扫一批近 7 天内提及兑换码的游戏分区非官方帖（前置过滤后才消耗 LLM）。"""
    cutoff = utcnow() - timedelta(days=window_days)
    feed_partitions = set(feed_community_keys())
    latest_parsed = select(func.max(ContentVersion.parsed_at)).where(
        ContentVersion.raw_content_id == RawContent.id,
    ).correlate(RawContent).scalar_subquery()

    keyword_cond = or_(
        ContentVersion.title.contains("兑换码"),
        ContentVersion.title.contains("礼包码"),
        ContentVersion.title.contains("口令码"),
        ContentVersion.text.contains("兑换码"),
        ContentVersion.text.contains("礼包码"),
        ContentVersion.text.contains("口令码"),
        func.lower(ContentVersion.text).contains("redemption code"),
        func.lower(ContentVersion.text).contains("redeem code"),
    )

    rows = (
        await session.execute(
            select(RawContent.id, RawContent.community)
            .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
            .join(Source, RawContent.source_id == Source.id, isouter=True)
            .where(
                or_(Source.identity.is_(None), Source.identity != "official"),
                RawContent.hidden.is_(False),
                RawContent.extracted_at.is_(None),
                RawContent.collected_at >= cutoff,
                *([RawContent.community.notin_(feed_partitions)] if feed_partitions else []),
                keyword_cond,
                retry_allowed("extract_community_codes", latest_parsed),
            )
            .order_by(RawContent.id.desc())
            .limit(limit * 4)
        )
    ).all()

    candidate_ids = [
        cid for cid, comm in rows if get_community(comm).domain == "game"
    ][: limit * 3]

    stats = {"candidates": 0, "codes": 0, "confirmed": 0, "failed": 0, "skipped": 0}
    llm_calls = 0
    for cid in candidate_ids:
        if llm_calls >= limit:
            break
        stats["candidates"] += 1
        result = await extract_community_codes(session, cid, notify=notify)
        if result["status"] == "skipped":
            stats["skipped"] += 1
            if result.get("reason") == "ai_not_configured":
                break
        elif result["status"] == "failed":
            stats["failed"] += 1
            llm_calls += 1
        else:
            stats["codes"] += result.get("codes", 0)
            stats["confirmed"] += result.get("confirmed", 0)
            llm_calls += 1
    return stats
