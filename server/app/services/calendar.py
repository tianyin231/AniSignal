"""日程展示与去重；未知结束时间不等于一直进行中。"""

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import community_conditions
from app.models import ContentSchedule, RawContent, Source


def schedule_topic(title: str, kind: str) -> str:
    title = unicodedata.normalize("NFKC", title).lower()
    version = re.search(r"(?<![\d.])\d+\.\d+(?![\d.])", title)
    if version and kind == "broadcast":
        return f"broadcast:{version.group()}"
    if version and (
        kind == "maintenance"
        or re.search(r"版本.*(?:更新|上线|开启)|version.*update|update.*version", title)
    ):
        return f"version:{version.group()}"
    title = re.sub(r"[（(](?:延期|延后|时间变更)[）)]", "", title)
    return re.sub(r"[^\w]", "", title)


def schedule_entry(schedule: ContentSchedule, url: str | None) -> dict:
    return {
        "id": schedule.id,
        "content_id": schedule.raw_content_id,
        "title": schedule.title,
        "kind": schedule.kind,
        "start_at": schedule.start_at,
        "end_at": schedule.end_at,
        "all_day": schedule.all_day,
        "delayed": schedule.delayed,
        "community": schedule.community,
        "url": url,
    }


async def calendar_entries(
    session: AsyncSession, community: str, *, now: datetime | None = None,
    window_start: datetime | None = None, window_end: datetime | None = None,
    extra_conditions: tuple = (), exclude: tuple[str, ...] = (),
) -> list[dict]:
    now = now or utcnow()
    window_start = window_start or now - timedelta(days=14)
    window_end = window_end or now + timedelta(days=90)
    conditions = [
        RawContent.hidden.is_(False),
        ContentSchedule.start_at < window_end,
        or_(ContentSchedule.start_at >= window_start, ContentSchedule.end_at >= window_start),
    ]
    conditions.extend(community_conditions(ContentSchedule.community, community, exclude))
    rows = await session.execute(
        select(ContentSchedule, RawContent.url)
        .join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
        .outerjoin(Source, Source.id == RawContent.source_id)
        .where(*conditions, *extra_conditions)
        .order_by(ContentSchedule.id)
    )
    unique: dict[tuple, dict] = {}
    for schedule, url in rows:
        if schedule.end_at and schedule.end_at < schedule.start_at:
            continue
        entry = schedule_entry(schedule, url)
        key = (schedule.community, schedule.start_at, schedule_topic(schedule.title, schedule.kind))
        previous = unique.get(key)
        # 同一时间、同一主题的跨平台公告保留一条，优先完整时间与中文标题。
        rank = lambda e: (bool(e["end_at"]), bool(re.search(r"[\u4e00-\u9fff]", e["title"])), e["kind"] != "delay")
        if previous is None or rank(entry) > rank(previous):
            unique[key] = entry
    return list(unique.values())


async def calendar_month(session: AsyncSession, community: str, month: str, *, exclude: tuple[str, ...] = ()) -> dict:
    first = datetime.strptime(month, "%Y-%m")
    # 月历从周一开始，固定六周；北京时间的日期边界转换为库内 UTC。
    grid_start = first - timedelta(days=first.weekday(), hours=8)
    items = await calendar_entries(session, community, window_start=grid_start,
                                   window_end=grid_start + timedelta(days=42), exclude=exclude)
    items.sort(key=lambda item: (item["start_at"], item["id"]))
    return {"month": month, "items": items}


async def calendar_groups(session: AsyncSession, community: str, *, exclude: tuple[str, ...] = ()) -> dict:
    now = utcnow()
    groups: dict[str, list] = {"ongoing": [], "upcoming": [], "recent": [], "ended": []}
    for entry in await calendar_entries(session, community, now=now, exclude=exclude):
        start, end = entry["start_at"], entry["end_at"]
        if start > now:
            groups["upcoming"].append(entry)
        elif entry["kind"] == "deadline":
            groups["ended"].append(entry)
        elif end is not None:
            groups["ongoing" if end > now else "ended"].append(entry)
        elif start >= now - timedelta(hours=48):
            groups["recent"].append(entry)
    groups["ongoing"].sort(key=lambda e: e["end_at"])
    groups["upcoming"].sort(key=lambda e: e["start_at"])
    groups["recent"].sort(key=lambda e: e["start_at"], reverse=True)
    groups["ended"].sort(key=lambda e: e["end_at"] or e["start_at"], reverse=True)
    return {key: values[:5 if key in {"recent", "ended"} else 10] for key, values in groups.items()}


_ICS_KIND_LABELS = {
    "activity": "活动",
    "maintenance": "更新维护",
    "pool": "卡池",
    "broadcast": "直播",
    "delay": "变更",
    "deadline": "截止",
}


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _ics_fold(line: str) -> str:
    parts, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " "
        current += char
    return "\r\n".join([*parts, current])


def _ics_vevent_lines(
    *,
    schedule_id: int,
    title: str,
    kind: str,
    start_at: datetime,
    end_at: datetime | None,
    all_day: bool,
    url: str | None,
    stamp: str,
    summary_prefix: str = "",
    detail_url: str | None = None,
) -> list[str]:
    summary = f"{summary_prefix}{title}" if summary_prefix else title
    lines = [
        "BEGIN:VEVENT",
        f"UID:schedule-{schedule_id}@anisignal",
        f"DTSTAMP:{stamp}",
        f"SUMMARY:{_ics_escape(summary)}",
    ]
    if all_day:
        start_day = (start_at + timedelta(hours=8)).date()
        lines.append(f"DTSTART;VALUE=DATE:{start_day:%Y%m%d}")
        if end_at:
            # 公告的结束日期包含当天，iCalendar 的 DTEND 不包含当天。
            end_day = (end_at + timedelta(hours=8)).date() + timedelta(days=1)
            lines.append(f"DTEND;VALUE=DATE:{end_day:%Y%m%d}")
    else:
        lines.append(f"DTSTART:{start_at:%Y%m%dT%H%M%SZ}")
        if end_at:
            lines.append(f"DTEND:{end_at:%Y%m%dT%H%M%SZ}")
    kind_label = _ICS_KIND_LABELS.get(kind)
    if kind_label:
        lines.append(f"CATEGORIES:{_ics_escape(kind_label)}")
    description = (
        "公告中的截止时间。"
        if kind == "deadline"
        else "结束时间未公布。"
        if end_at is None
        else "以来源公告为准。"
    )
    clean_url = url.replace("\r", "").replace("\n", "") if url else None
    clean_detail = detail_url.replace("\r", "").replace("\n", "") if detail_url else None
    if clean_url:
        description += f"\n来源：{clean_url}"
    if clean_detail and clean_detail != clean_url:
        description += f"\n站内详情：{clean_detail}"
    event_url = clean_url or clean_detail
    if event_url:
        lines.append(f"URL:{event_url}")
    lines.extend([f"DESCRIPTION:{_ics_escape(description)}", "TRANSP:TRANSPARENT", "END:VEVENT"])
    return lines


def calendar_ics(schedule: ContentSchedule, url: str | None) -> str:
    """RFC 5545：UTC 时刻、全天日期、CRLF 和 UTF-8 按字节折行。"""
    stamp = f"{utcnow():%Y%m%dT%H%M%SZ}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AniSignal//Game Calendar//ZH",
        "CALSCALE:GREGORIAN",
        *_ics_vevent_lines(
            schedule_id=schedule.id,
            title=schedule.title,
            kind=schedule.kind,
            start_at=schedule.start_at,
            end_at=schedule.end_at,
            all_day=schedule.all_day,
            url=url,
            stamp=stamp,
        ),
        "END:VCALENDAR",
    ]
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


def calendar_feed_ics(
    entries: list[dict],
    *,
    cal_name: str,
    community_labels: dict[str, str] | None = None,
    site_base: str | None = None,
    include_community_prefix: bool = False,
    limit: int = 200,
) -> str:
    """整分区/全站 Webcal 订阅流（RFC 5545 + RFC 7986），支持 Apple/Google/Outlook 自动刷新。"""
    stamp = f"{utcnow():%Y%m%dT%H%M%SZ}"
    base = (site_base or "").rstrip("/")
    labels = community_labels or {}
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AniSignal//Game Calendar//ZH",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(cal_name)}",
        f"NAME:{_ics_escape(cal_name)}",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    ordered = sorted(entries, key=lambda e: (e["start_at"], e["id"]))[:limit]
    for entry in ordered:
        prefix = ""
        if include_community_prefix:
            label = labels.get(entry.get("community") or "")
            if label:
                prefix = f"[{label}] "
        detail = f"{base}/contents/{entry['content_id']}" if base and entry.get("content_id") else None
        lines.extend(
            _ics_vevent_lines(
                schedule_id=entry["id"],
                title=entry["title"],
                kind=entry["kind"],
                start_at=entry["start_at"],
                end_at=entry.get("end_at"),
                all_day=bool(entry.get("all_day")),
                url=entry.get("url"),
                stamp=stamp,
                summary_prefix=prefix,
                detail_url=detail,
            )
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"
