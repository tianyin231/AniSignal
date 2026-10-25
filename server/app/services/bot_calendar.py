"""每天一条仍可行动的日程清单；过期事项不追补。"""

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import ContentSchedule, RawContent
from app.models.enums import COMMUNITY_LABELS
from app.services.calendar import calendar_entries


def scheduled_at(now: datetime, clock: str) -> datetime:
    hour, minute = map(int, clock.split(":"))
    return (now + timedelta(hours=8)).replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(hours=8)


def normalized_title(title: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", title).casefold())


def stage_at(entry: dict) -> datetime | None:
    if entry["kind"] in {"maintenance", "deadline"}:
        return entry["start_at"]
    if entry["kind"] in {"activity", "pool"}:
        return entry["end_at"]
    return None


def merge_entries(entries: list[dict]) -> list[dict]:
    merged = []
    for entry in sorted(entries, key=lambda e: (e["stage_at"], len(e["title"]))):
        topic = normalized_title(entry["title"])
        same = next((old for old in merged if old["community"] == entry["community"]
                     and old["stage_at"] == entry["stage_at"] and old["kind"] == entry["kind"]
                     and (topic in normalized_title(old["title"]) or normalized_title(old["title"]) in topic)), None)
        if same:
            same["schedule_ids"] = sorted(set(same["schedule_ids"] + entry["schedule_ids"]))
            same["content_ids"] = sorted(set(same["content_ids"] + entry["content_ids"]))
        else:
            merged.append(dict(entry))
    return merged


async def upcoming_entries(session, community: str, now: datetime) -> list[dict]:
    entries = []
    for entry in await calendar_entries(session, community, now=now):
        if entry["all_day"]:
            continue
        due = stage_at(entry)
        if not due or not now < due <= now + timedelta(hours=24):
            continue
        if entry["kind"] in {"activity", "pool"} and entry["start_at"] > now:
            continue
        entries.append({"title": entry["title"], "kind": entry["kind"], "community": entry["community"],
                        "stage_at": due.isoformat(), "expires_at": due.isoformat(), "url": entry["url"],
                        "schedule_ids": [entry["id"]], "content_ids": [entry["content_id"]]})
    return merge_entries(entries)


def calendar_brief(entries: list[dict], due: datetime) -> tuple[str, dict]:
    ending = sum(entry["kind"] != "maintenance" for entry in entries)
    maintenance = len(entries) - ending
    overview = []
    if ending:
        overview.append(f"{ending} 项活动或卡池将截止")
    if maintenance:
        overview.append(f"{maintenance} 项维护将开始")
    lines = ["明日前的小提醒", "未来 24 小时，" + "，".join(overview) + "。"]
    groups = {}
    for entry in entries:
        key = (entry["community"], entry["stage_at"], entry["kind"] == "maintenance")
        groups.setdefault(key, []).append(entry)
    truncated = len(groups) > 2
    for (community, when, maintenance_start), group in list(groups.items())[:2]:
        label = COMMUNITY_LABELS.get(community, community)
        when = (datetime.fromisoformat(when) + timedelta(hours=8)).strftime("%m/%d %H:%M")
        names = []
        for entry in group[:2]:
            match = re.search(r"[「『\[【](.*?)[」』\]】]", entry["title"])
            name = match.group(1) if match else entry["title"]
            if len(name) <= 15:
                names.append(name)
        topic = "、".join(names)
        truncated = truncated or len(group) > len(names)
        if len(group) > len(names) and topic:
            topic += "等"
        action = "开始维护" if maintenance_start else f"{len(group)} 项截止"
        lines.append(f"· [{label}] {when} {action}" + (f"：{topic}" if topic else ""))
    if len(groups) > 2:
        lines.append(f"另有 {len(groups) - 2} 组日程。")
    text = "\n".join(lines)
    brief = {"mode": "calendar", "title": "明日前的小提醒", "date": (due + timedelta(hours=8)).strftime("%m/%d %H:%M"),
             "scheduled_at": due.isoformat(), "expires_at": max(entry["expires_at"] for entry in entries), "truncated": truncated,
             "items": [{"title": "明日前的小提醒", "summary": "，".join(overview), "community": "",
                        "calendar_entries": entries, "sources": entries}]}
    return text, brief


async def refresh_calendar(session, brief: dict, now: datetime) -> tuple[str, dict] | None:
    entries = [entry for item in brief.get("items", []) for entry in item.get("calendar_entries", [])]
    ids = {sid for entry in entries for sid in entry.get("schedule_ids", [])}
    rows = (await session.execute(select(ContentSchedule, RawContent.url).join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
                                  .where(ContentSchedule.id.in_(ids), RawContent.hidden.is_(False)))).all() if ids else []
    current = {schedule.id: (schedule, url) for schedule, url in rows}
    valid = []
    for entry in entries:
        for sid in entry.get("schedule_ids", []):
            if sid not in current:
                continue
            schedule, url = current[sid]
            due = schedule.start_at if schedule.kind in {"maintenance", "deadline"} else schedule.end_at
            if schedule.all_day or schedule.kind not in {"activity", "pool", "deadline", "maintenance"} or not due or due <= now:
                continue
            # 时间已经改动，旧清单不能继续宣称原时间；留给下一次清单。
            if due.isoformat() != entry["stage_at"]:
                continue
            valid.append({**entry, "schedule_ids": [sid], "content_ids": [schedule.raw_content_id], "url": url})
    valid = merge_entries(valid)
    return calendar_brief(valid, datetime.fromisoformat(brief["scheduled_at"])) if valid else None
