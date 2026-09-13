"""官方内容实体抽取：AI 结构化提取活动/维护/卡池日程与兑换码。

设计约束：
- 只处理官方身份内容（Source.identity=official），非官方不抽取。
- 抽取过的内容置 raw_contents.extracted_at，无论结果是否为空都算完成（不重复付费）。
- 新兑换码 / 维护延期即时进入 bot 推送通道（updates 类，按目标 community 匹配）。
- AI 失败安全：无配置/超时/解析失败只影响当轮，内容仍标记已抽取。
"""

import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.models import BotTarget, ContentSchedule, RawContent, RedeemCode, Source, ContentVersion
from app.services.ai import ai_available, chat, get_ai_config, record_usage

logger = logging.getLogger(__name__)

EXTRACT_WINDOW_DAYS = 30
EXTRACT_BATCH_LIMIT = 10
_TEXT_LIMIT = 6000
_KINDS = {"activity", "maintenance", "pool", "broadcast", "delay"}
_CODE_PATTERN_MIN = 6
_CODE_PATTERN_MAX = 20

_SYSTEM = "你是游戏公告结构化助手。严格按用户要求的 JSON 格式输出，不编造任何不存在的信息。"

_USER_TEMPLATE = """从以下官方内容中抽取「日程」与「兑换码」，输出 JSON：
{{"schedules": [{{"title": "...", "start_at": "...", "end_at": "...", "kind": "..."}}], "codes": [{{"code": "...", "expires_at": "..."}}]}}

规则：
1. schedules 只收有明确日期/时间点的日程。kind 只能取：activity（游戏内活动/任务/商城）、maintenance（停机维护/版本更新）、pool（卡池/祈愿/建造）、broadcast（直播/前瞻节目）、delay（延期公告：标题或正文含"延迟/延长/推迟"且给出新时间）。
2. 时间一律输出 ISO 格式并带 +08:00 时区（例如 2026-09-12T11:00:00+08:00）。文中未注明年份按 {year} 年理解；"X月X日 11:00"这类只有开始时间的，end_at 填 null。
3. delay 条目的 title 写「原活动名（延期）」，start_at 填新的时间。
4. codes 只在正文出现"兑换码/礼包码"字样时抽取，码为 6-20 位字母数字组合；expires_at 无明确时间填 null。
5. 没有可抽取的内容就输出空列表。绝不编造正文里不存在的码或时间。

标题：{title}
正文：{text}"""


def _parse_extraction(raw_out: str) -> tuple[list[dict], list[dict]]:
    """健壮解析抽取结果（容忍 ```json 围栏与字段缺失）。"""
    text = (raw_out or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [], []
    schedules: list[dict] = []
    for entry in payload.get("schedules") or []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()[:200]
        kind = str(entry.get("kind") or "").strip()
        start = _parse_dt(entry.get("start_at"))
        if not title or kind not in _KINDS or start is None:
            continue
        schedules.append(
            {
                "title": title,
                "kind": kind,
                "start_at": start,
                "end_at": _parse_dt(entry.get("end_at")),
            }
        )
    codes: list[dict] = []
    for entry in payload.get("codes") or []:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "").strip()
        if not (_CODE_PATTERN_MIN <= len(code) <= _CODE_PATTERN_MAX and code.isalnum()):
            continue
        codes.append({"code": code, "expires_at": _parse_dt(entry.get("expires_at"))})
    return schedules, codes


def _parse_dt(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return to_naive_utc(dt)


async def _targets_for_community(session: AsyncSession, community: str) -> list[BotTarget]:
    rows = await session.scalars(
        select(BotTarget).where(BotTarget.enabled.is_(True))
    )
    targets = []
    for target in rows:
        if not target.config.get("updates_enabled"):
            continue
        target_community = target.config.get("community", "all")
        if target_community in ("all", community):
            targets.append(target)
    return targets


async def _push_to_targets(
    session: AsyncSession, community: str, kind: str, text: str, dedupe_key: str
) -> int:
    from app.services.bot import create_delivery

    delivered = 0
    for target in await _targets_for_community(session, community):
        await create_delivery(session, target, kind, text, dedupe_key)
        delivered += 1
    return delivered


async def extract_entities(session: AsyncSession, content_id: int) -> dict:
    """抽取单条官方内容的日程与兑换码；完成后置 extracted_at。"""
    row = await session.execute(
        select(RawContent, ContentVersion, Source.identity)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(RawContent.id == content_id)
        .order_by(ContentVersion.version.desc())
        .limit(1)
    )
    pair = row.first()
    if pair is None:
        return {"status": "skipped", "reason": "content_not_found"}
    raw, version, identity = pair
    if (identity or "unknown") != "official":
        return {"status": "skipped", "reason": "not_official"}
    title = (version.title if version else None) or ""
    text = ((version.text if version else None) or "")[:_TEXT_LIMIT]
    if len(f"{title}{text}".strip()) < 30:
        raw.extracted_at = utcnow()
        await session.commit()
        return {"status": "skipped", "reason": "nothing_to_extract"}

    config = await get_ai_config(session)
    if not ai_available(config):
        return {"status": "skipped", "reason": "ai_not_configured"}

    user = _USER_TEMPLATE.format(year=utcnow().year, title=title, text=text)
    started = time.monotonic()
    try:
        raw_out, usage = await chat(config, _SYSTEM, user, json_mode=True)
    except Exception as exc:  # noqa: BLE001
        await record_usage(
            session, task="extract_entities", config=config, usage=None,
            duration_ms=int((time.monotonic() - started) * 1000),
            ref_id=content_id, status="failed",
        )
        raw.extracted_at = utcnow()
        await session.commit()
        return {"status": "failed", "reason": str(exc)[:200]}
    await record_usage(
        session, task="extract_entities", config=config, usage=usage,
        duration_ms=int((time.monotonic() - started) * 1000), ref_id=content_id,
    )

    schedules, codes = _parse_extraction(raw_out)
    community = raw.community or "wuwa"
    now = utcnow()
    new_schedules = 0
    for entry in schedules:
        exists = await session.scalar(
            select(ContentSchedule.id).where(
                ContentSchedule.raw_content_id == content_id,
                ContentSchedule.title == entry["title"],
            )
        )
        if exists:
            continue
        session.add(
            ContentSchedule(
                raw_content_id=content_id,
                community=community,
                title=entry["title"],
                kind=entry["kind"],
                start_at=entry["start_at"],
                end_at=entry["end_at"],
                delayed=entry["kind"] == "delay",
                created_at=now,
            )
        )
        new_schedules += 1

    new_codes = 0
    for entry in codes:
        exists = await session.scalar(
            select(RedeemCode.id).where(
                RedeemCode.community == community, RedeemCode.code == entry["code"]
            )
        )
        if exists:
            continue
        session.add(
            RedeemCode(
                raw_content_id=content_id,
                community=community,
                code=entry["code"],
                status="active",
                expires_at=entry["expires_at"],
                created_at=now,
            )
        )
        new_codes += 1
        pushed = await _push_to_targets(
            session,
            community,
            "critical",
            f"🎫 新兑换码：{entry['code']}\n来自：{title}\n记得尽快兑换，过期作废。",
            f"code:{community}:{entry['code']}",
        )
        logger.info("new redeem code %s pushed to %s targets", entry["code"], pushed)

    # 延期公告：把同社区进行中/临近的维护与活动标记 delayed 并即时推送
    for entry in schedules:
        if entry["kind"] != "delay":
            continue
        ongoing = await session.scalars(
            select(ContentSchedule).where(
                ContentSchedule.community == community,
                ContentSchedule.kind.in_(["maintenance", "activity"]),
                ContentSchedule.delayed.is_(False),
                ContentSchedule.start_at <= entry["start_at"],
            )
        )
        marked = 0
        for schedule in ongoing:
            schedule.delayed = True
            marked += 1
        if marked:
            pushed = await _push_to_targets(
                session,
                community,
                "critical",
                f"⚠️ 日程变更：{entry['title']}\n新时间：{(entry['start_at'] + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M')}（北京时间）",
                f"delay:{community}:{content_id}:{int(entry['start_at'].timestamp())}",
            )
            logger.info("delay notice pushed for %s schedules (%s targets)", marked, pushed)

    raw.extracted_at = utcnow()
    await session.commit()
    return {"status": "ok", "schedules": new_schedules, "codes": new_codes}


async def sweep_extractions(session: AsyncSession, limit: int = EXTRACT_BATCH_LIMIT) -> dict:
    """扫一批待抽取的官方内容（30 天内、未抽取、未下架）。"""
    cutoff = utcnow() - timedelta(days=EXTRACT_WINDOW_DAYS)
    ids = list(
        await session.scalars(
            select(RawContent.id)
            .join(Source, RawContent.source_id == Source.id)
            .where(
                Source.identity == "official",
                RawContent.hidden.is_(False),
                RawContent.extracted_at.is_(None),
                RawContent.collected_at >= cutoff,
            )
            .order_by(RawContent.id.desc())
            .limit(limit)
        )
    )
    processed = {"schedules": 0, "codes": 0, "failed": 0, "skipped": 0}
    for content_id in ids:
        result = await extract_entities(session, content_id)
        if result["status"] == "failed":
            processed["failed"] += 1
        elif result["status"] == "skipped":
            processed["skipped"] += 1
        else:
            processed["schedules"] += result.get("schedules", 0)
            processed["codes"] += result.get("codes", 0)
    return {"candidates": len(ids), **processed}
