"""日程展示与去重；未知结束时间不等于一直进行中。"""

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import ContentSchedule, RawContent


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


async def calendar_entries(session: AsyncSession, community: str, *, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    conditions = [
        RawContent.hidden.is_(False),
        ContentSchedule.start_at <= now + timedelta(days=90),
        or_(ContentSchedule.start_at >= now - timedelta(days=14),
            ContentSchedule.end_at >= now - timedelta(days=14)),
    ]
    if community != "all":
        conditions.append(ContentSchedule.community == community)
    rows = await session.execute(
        select(ContentSchedule, RawContent.url)
        .join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
        .where(*conditions)
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


async def calendar_groups(session: AsyncSession, community: str) -> dict:
    now = utcnow()
    groups: dict[str, list] = {"ongoing": [], "upcoming": [], "recent": [], "ended": []}
    for entry in await calendar_entries(session, community, now=now):
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


def calendar_ics(schedule: ContentSchedule, url: str | None) -> str:
    """RFC 5545：UTC 时刻、全天日期、CRLF 和 UTF-8 按字节折行。"""
    def text(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")

    def fold(line: str) -> str:
        parts, current = [], ""
        for char in line:
            if len((current + char).encode("utf-8")) > 75:
                parts.append(current)
                current = " "
            current += char
        return "\r\n".join([*parts, current])

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AniSignal//Game Calendar//ZH", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
             f"UID:schedule-{schedule.id}@anisignal", f"DTSTAMP:{utcnow():%Y%m%dT%H%M%SZ}", f"SUMMARY:{text(schedule.title)}"]
    start, end = schedule.start_at, schedule.end_at
    if schedule.all_day:
        start_day = (start + timedelta(hours=8)).date()
        lines.append(f"DTSTART;VALUE=DATE:{start_day:%Y%m%d}")
        if end:
            # 公告的结束日期包含当天，iCalendar 的 DTEND 不包含当天。
            end_day = (end + timedelta(hours=8)).date() + timedelta(days=1)
            lines.append(f"DTEND;VALUE=DATE:{end_day:%Y%m%d}")
    else:
        lines.append(f"DTSTART:{start:%Y%m%dT%H%M%SZ}")
        if end:
            lines.append(f"DTEND:{end:%Y%m%dT%H%M%SZ}")
    description = "公告中的截止时间。" if schedule.kind == "deadline" else "结束时间未公布。" if end is None else "以来源公告为准。"
    if url:
        description += f"\n来源：{url}"
        lines.append(f"URL:{url.replace(chr(13), '').replace(chr(10), '')}")
    lines.extend([f"DESCRIPTION:{text(description)}", "TRANSP:TRANSPARENT", "END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
