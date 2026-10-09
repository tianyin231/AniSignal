"""官方内容实体抽取：AI 结构化提取活动/维护/卡池日程与兑换码。

设计约束：
- 只处理官方身份内容（Source.identity=official），非官方不抽取。
- 抽取过的内容置 raw_contents.extracted_at，无论结果是否为空都算完成（不重复付费）。
- 新兑换码 / 维护延期即时进入 bot 推送通道（updates 类，按目标 community 匹配）。
- AI 失败安全：无配置/超时/解析失败只影响当轮，内容仍标记已抽取。
"""

import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.models import BotTarget, ContentSchedule, RawContent, RedeemCode, Source, ContentVersion
from app.services.ai import ai_available, chat, get_ai_config, record_usage

logger = logging.getLogger(__name__)

EXTRACT_WINDOW_DAYS = 30
EXTRACT_BATCH_LIMIT = 10
_TEXT_LIMIT = 6000
_KINDS = {"activity", "maintenance", "pool", "broadcast", "delay", "deadline"}
_CODE_PATTERN_MIN = 6
_CODE_PATTERN_MAX = 20

_SYSTEM = "你是游戏公告结构化助手。严格按用户要求的 JSON 格式输出，不编造任何不存在的信息。"

_USER_TEMPLATE = """从以下官方内容中抽取「日程」与「兑换码」，输出 JSON：
{{"schedules": [{{"title": "...", "start_at": "...", "end_at": "...", "kind": "...", "all_day": false, "date_evidence": "含日期的原文片段"}}], "codes": [{{"code": "...", "expires_at": "..."}}]}}

规则：
1. schedules 只收有明确日期/时间点的日程。kind 只能取：activity（游戏内活动/任务/商城）、maintenance（停机维护/版本更新）、pool（卡池/祈愿/建造）、broadcast（直播/前瞻节目）、delay（明确延期并给出新时间）、deadline（只公布报名、兑换、抽奖等截止时间，不能当作开始时间）。
2. 时间输出带时区的 ISO 格式；原文有时区必须遵守，否则使用北京时间 +08:00。公告发布时间为 {published_at}；“今晚/明天”等相对时间必须基于公告发布时间，不能基于当前时间。未注明年份按公告的 {year} 年理解；未给结束时间填 null。
3. delay 条目的 title 写「原活动名（延期）」，start_at 填新的时间。
4. codes 只在正文出现"兑换码/礼包码"字样时抽取，码为 6-20 位字母数字组合；expires_at 无明确时间填 null。
5. 没有可抽取的内容就输出空列表。绝不编造正文里不存在的码或时间。
6. 只有日期没有具体时刻时，all_day=true，start_at 使用该日期的 00:00，不得编造维护开服时刻。只有“某版本期间”而没有日期的，不抽取。date_evidence 必须逐字摘录支持开始和结束日期的原文，不能改写。

标题：{title}
正文：{text}"""


def _date_supported(value: str, evidence: str, published_at: datetime) -> bool:
    """校验数字/中英文日期，以及以公告发布时间为基准的相对日期。"""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        return False
    evidence = unicodedata.normalize("NFKC", evidence).lower()
    published = published_at.replace(tzinfo=timezone.utc).astimezone(dt.tzinfo)
    dates = []
    for match in re.finditer(r"(?<![\d.])(?:(\d{4})[-/.年])?(\d{1,2})[-/.月](\d{1,2})(?:日|号)?(?![\d.])", evidence):
        # 版本号（如 3.7 版本）不是 3 月 7 日。
        if re.search(r"版本\s*$|version\s*$", evidence[:match.start()]) or re.match(r"\s*(?:版本|version)", evidence[match.end():]):
            continue
        y, m, d = match.groups()
        dates.append((int(y) if y else published.year, int(m), int(d)))
    months = "january february march april may june july august september october november december".split()
    for month, name in enumerate(months, 1):
        for day, year in re.findall(rf"\b(?:{name}|{name[:3]}\.?)\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", evidence):
            dates.append((int(year) if year else published.year, month, int(day)))
    for day in re.findall(r"(?:至|到|[-~–])\s*(\d{1,2})[日号]", evidence):
        dates.extend((y, m, int(day)) for y, m, _ in list(dates))
    if dates:
        return (dt.year, dt.month, dt.day) in dates
    for pattern, offset in [(r"后天", 2), (r"明天|明晚|明日|tomorrow", 1), (r"今天|今晚|今日|本日|today|tonight", 0)]:
        if re.search(pattern, evidence):
            return dt.date() == (published + timedelta(days=offset)).date()
    return False


def _parse_extraction(raw_out: str, *, source_text: str | None = None, published_at: datetime | None = None) -> tuple[list[dict], list[dict]]:
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
    if not isinstance(payload, dict):
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
        end = _parse_dt(entry.get("end_at"))
        if end is not None and end < start:
            continue
        if source_text is not None:
            evidence = str(entry.get("date_evidence") or "").strip()
            compact = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()
            if not evidence or compact(evidence) not in compact(source_text):
                continue
            anchor = published_at or utcnow()
            if not _date_supported(entry.get("start_at"), evidence, anchor):
                continue
            if end is not None and not _date_supported(entry.get("end_at"), evidence, anchor):
                continue
            if entry.get("all_day") is not True and not re.search(r"\d{1,2}\s*[:：]\s*\d{2}|\d{1,2}\s*[点时]", evidence):
                continue
        if entry.get("all_day") is True:
            # 日期范围包含结束当天；内部边界不作为具体时刻展示或提醒。
            start_day = (start + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
            start = start_day - timedelta(hours=8)
            if kind == "deadline":
                start += timedelta(days=1, seconds=-1)
            if end is not None:
                end_day = (end + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
                end = end_day + timedelta(hours=16, seconds=-1)
        schedules.append(
            {
                "title": title,
                "kind": kind,
                "start_at": start,
                "end_at": end,
                "all_day": entry.get("all_day") is True,
            }
        )
    codes: list[dict] = []
    for entry in payload.get("codes") or []:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "").strip()
        if not (_CODE_PATTERN_MIN <= len(code) <= _CODE_PATTERN_MAX and re.fullmatch(r"[A-Za-z0-9]+", code)):
            continue
        if source_text is not None and code.lower() not in source_text.lower():
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
    session: AsyncSession, community: str, kind: str, text: str, dedupe_key: str, *, brief: dict | None = None
) -> int:
    from app.services.bot import create_delivery

    delivered = 0
    for target in await _targets_for_community(session, community):
        await create_delivery(session, target, kind, text, f"{dedupe_key}:target:{target.id}", brief=brief)
        delivered += 1
    return delivered


async def extract_entities(session: AsyncSession, content_id: int, *, notify: bool = True) -> dict:
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

    published = raw.published_at or raw.collected_at
    # 日期解析的内部锚点不能当作消息对外展示的发布时间。
    source_published = raw.published_at.isoformat() if raw.published_at else None
    published_bj = published + timedelta(hours=8)
    user = _USER_TEMPLATE.format(year=published_bj.year, published_at=f"{published_bj:%Y/%m/%d %H:%M}（北京时间）", title=title, text=text)
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

    schedules, codes = _parse_extraction(raw_out, source_text=f"{title}\n{text}", published_at=published)
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
                all_day=entry["all_day"],
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
        expiry = entry["expires_at"]
        source = {"id": content_id, "content_ids": [content_id], "title": title, "text": text,
                  "url": raw.url, "community": community, "identity": "official", "published_at": source_published}
        pushed = await _push_to_targets(
            session,
            community,
            "critical",
            f"🎫 新兑换码：{entry['code']}\n来自：{title}\n记得尽快兑换，过期作废。",
            f"code:{community}:{entry['code']}",
            brief={"mode": "notice", "code": entry["code"], "expires_at": min(expiry or now + timedelta(hours=12), now + timedelta(hours=12)).isoformat(),
                   "items": [{**source, "sources": [source]}]},
        ) if notify and (not expiry or expiry > now) else 0
        logger.info("new redeem code %s pushed to %s targets", entry["code"], pushed)

    # 仅修改明确同主题的日程，不能把同游戏的所有活动一起标成延期。
    from app.services.calendar import schedule_topic
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
            if schedule_topic(schedule.title, schedule.kind) != schedule_topic(entry["title"], entry["kind"]):
                continue
            if schedule.end_at and schedule.end_at < published:
                continue
            schedule.delayed = True
            schedule.start_at = entry["start_at"]
            schedule.end_at = entry["end_at"]
            schedule.all_day = entry["all_day"]
            marked += 1
        if marked and notify:
            pushed = await _push_to_targets(
                session,
                community,
                "critical",
                f"⚠️ 日程变更：{entry['title']}\n新时间：{(entry['start_at'] + timedelta(hours=8)).strftime('%Y/%m/%d %H:%M')}（北京时间）",
                f"delay:{community}:{content_id}:{int(entry['start_at'].timestamp())}",
                brief={"mode": "notice", "expires_at": min(entry["start_at"], now + timedelta(hours=12)).isoformat(),
                       "items": [{"title": entry["title"], "community": community, "content_ids": [content_id],
                                  "sources": [{"title": title, "text": text, "content_ids": [content_id],
                                               "url": raw.url, "community": community, "identity": "official", "published_at": source_published}]}]},
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
