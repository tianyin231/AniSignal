"""AI 资讯的统一读取与成刊；读取页面不会调用模型。"""

from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.clock import utcnow
from app.models import CollectTarget, Evidence, NewsEdition
from app.services.editorial_calls import fingerprint, get_policy
from app.services.ai_news import news_review_identity, reference_links
from app.services.news_editorial import SCORE_VERSION, first_party, load_news_records, representative

EDITION_VERSION = "news-daily-v1"


def source_item(row):
    return {"id": row.raw.id, "headline": row.news["headline"], "summary": row.news["summary"],
        "original_title": row.version.title or "", "kind": row.news["kind"], "topics": row.news["topics"],
        "platform": row.raw.platform, "source_name": row.source.name if row.source and row.source.name else "来源未署名",
        "source_role": "original" if row.raw.platform == "x" else "secondary", "verification": "pending",
        "published_at": row.raw.published_at, "url": row.raw.url, "references": reference_links(row.version.text or "")}


def participant(row):
    if not row.source:
        return None
    if row.source and row.source.owner_key:
        return "owner:" + row.source.owner_key
    return f"source:{row.source.id}"


def heat_at(records, at):
    """每个独立参与方取窗口内最新发布时间；48 小时窗口、24 小时半衰期。"""
    latest = {}
    for row in records:
        published = row.raw.published_at
        if at - timedelta(hours=48) < published <= at:
            key = participant(row)
            if key is None:
                continue
            latest[key] = max(latest.get(key, datetime.min), published)
    heat = sum(0.5 ** ((at - published).total_seconds() / 86400) for published in latest.values())
    return round(heat * 10, 1), len(latest)


def selected_score(row, policy):
    if not row.current or not row.event or not row.event.is_active:
        return None, False
    value = row.news.get("selection") or {}
    scores = value.get("scores") or []
    threshold = policy["first_party_threshold" if first_party(row) else "media_threshold"]
    if not policy["score_enabled"]:
        return row.review.quality_score, row.current and row.review.quality_score >= threshold
    if value.get("prompt_version") != SCORE_VERSION or len(scores) != 2:
        return None, False
    return sum(scores) // 2, sum(scores) >= threshold * 2


async def visible_records(session, *, now=None, days=30):
    records = await load_news_records(session, since=(now or utcnow()) - timedelta(days=days),
        until=(now or utcnow()) + timedelta(microseconds=1))
    excluded = set(await session.scalars(select(Evidence.raw_content_id).where(Evidence.role == "excluded")))
    return [row for row in records if row.raw.id not in excluded
        and not (row.current and row.event and not row.event.is_active)]


async def list_stories(session, *, q="", topic="", kind="all", hours=168, sort="time", selected=False, now=None):
    now = now or utcnow()
    policy = await get_policy(session)
    records = await visible_records(session, now=now, days=max(30, (hours + 23) // 24))
    pools = {}
    for row in records:
        if row.current and row.grouping.relation == "ROUNDUP":
            continue
        key = ("story", row.grouping.story_id or row.grouping.event_id) if row.current and row.event else ("content", row.raw.id)
        pools.setdefault(key, []).append(row)
    targets = list(await session.scalars(select(CollectTarget).where(CollectTarget.community == "ai", CollectTarget.enabled.is_(True))))
    complete = bool(targets) and all(t.last_success_at and not t.consecutive_failures
        and t.last_success_at >= now - timedelta(minutes=max(90, t.interval_minutes * 3)) for t in targets)
    result = []
    for (key_type, key_id), rows in pools.items():
        recent = [r for r in rows if r.raw.published_at >= now - timedelta(hours=hours)]
        if not recent:
            continue
        matched = [r for r in rows if (kind == "all" or r.news["kind"] == kind)
            and (not topic or topic in r.news["topics"])
            and (not q or q.casefold() in " ".join((r.news["headline"], r.news["summary"],
                r.version.title or "", r.version.text or "", r.source.name if r.source and r.source.name else "")).casefold())]
        if not matched:
            continue
        # 筛选在事件归组后应用，命中后仍保留该事件的其他有效来源与进展。
        occurrences = {}
        for row in rows:
            event_id = row.grouping.event_id if row.current and row.event else -row.raw.id
            occurrences.setdefault(event_id, []).append(row)
        developments = []
        for event_id, members in occurrences.items():
            rep = representative(members)
            score, eligible = max((selected_score(r, policy) for r in members), key=lambda value: (value[1], value[0] or 0))
            copy = source_item(rep)
            if rep.event and rep.event.manual_locked:
                copy["headline"] = rep.event.title
                copy["summary"] = rep.event.summary or copy["summary"]
            developments.append({**copy, "event_id": event_id if event_id > 0 else None,
                "score": score, "selected": eligible, "sources": [source_item(r) for r in members],
                "occurred_at": min(r.raw.published_at for r in members),
                "latest_at": max(r.raw.published_at for r in members)})
        developments.sort(key=lambda item: (item["occurred_at"], item["id"]), reverse=True)
        eligible = any(item["selected"] for item in developments)
        if selected and not eligible:
            continue
        rep = developments[0]
        heat, participants = heat_at(rows, now)
        if sort == "hot" and participants < 2:
            continue
        previous_heat, _ = heat_at(rows, now - timedelta(hours=6))
        established = all(r.source and r.source.first_seen_at <= now - timedelta(hours=54) for r in rows)
        comparable = complete and established
        trend_pct = round((heat - previous_heat) / previous_heat * 100, 1) if comparable and previous_heat else None
        first_at = min(r.raw.published_at for r in rows)
        trend = ("new" if first_at > now - timedelta(hours=6) else "unknown" if not comparable or trend_pct is None
            else "up" if trend_pct > 10 else "down" if trend_pct < -10 else "flat")
        result.append({**rep, "story_id": key_id if key_type == "story" else None,
            "sources": [source_item(r) for r in sorted(rows, key=lambda row: row.raw.published_at, reverse=True)],
            "developments": developments, "report_count": len(rows), "participant_count": participants,
            "heat": heat, "trend": trend, "trend_pct": trend_pct,
            "first_at": first_at, "latest_at": max(d["occurred_at"] for d in developments),
            "updated_at": max(r.review.updated_at for r in rows), "selected": eligible,
            "score": max((d["score"] for d in developments if d["score"] is not None), default=None)})
    if sort == "hot":
        result.sort(key=lambda item: (item["heat"], item["latest_at"], item["id"]), reverse=True)
    else:
        result.sort(key=lambda item: (item["latest_at"], item["id"]), reverse=True)
    return result


async def compose_news_edition(session, *, now=None):
    """北京时间 08:00 出刊，沿用 07:50 截稿；已出刊只读取，不重写。"""
    now = now or utcnow()
    local = now + timedelta(hours=8)
    day = local.date() if local.hour >= 8 else (local - timedelta(days=1)).date()
    existing = await session.scalar(select(NewsEdition).where(NewsEdition.community == "ai", NewsEdition.day == day))
    if existing:
        return existing
    end = datetime.combine(day, time(7, 50)) - timedelta(hours=8)
    start = end - timedelta(days=1)
    records = await visible_records(session, now=now)
    policy = await get_policy(session)
    if not policy["enabled"]:
        return None
    eligible = []
    for record in records:
        if not record.current or not record.event or record.grouping.relation == "ROUNDUP":
            continue
        available_at = max(record.raw.published_at, record.review.updated_at, record.grouping.created_at)
        if policy["score_enabled"]:
            selection = record.news.get("selection") or {}
            if selection.get("prompt_version") != SCORE_VERSION:
                continue
            if selection.get("scored_at"):
                available_at = max(available_at, datetime.fromisoformat(selection["scored_at"]))
        if start <= available_at < end:
            eligible.append(record)
    if not eligible:
        return None
    history = list(await session.scalars(select(NewsEdition).where(
        NewsEdition.community == "ai", NewsEdition.day < day, NewsEdition.day >= day - timedelta(days=7))))
    used = {eid for issue in history for item in issue.content.get("items", []) for eid in item["event_ids"]}
    occurrences = {}
    for record in eligible:
        if record.event.id not in used and selected_score(record, policy)[1]:
            occurrences.setdefault(record.grouping.story_id or record.event.id, []).append(record)
    items = []
    for story_id, members in occurrences.items():
        developments = {}
        for member in members:
            developments.setdefault(member.event.id, []).append(member)
        latest = max(developments.values(), key=lambda rows: min(r.raw.published_at for r in rows))
        rep = representative(latest)
        heat, _ = heat_at(members, end)
        items.append({**source_item(rep), "story_id": story_id,
            "event_ids": sorted({r.event.id for r in members}), "score": max(selected_score(r, policy)[0] or 0 for r in members),
            "heat": heat, "source_versions": {str(r.raw.id): [r.raw.content_hash, r.version.version,
                fingerprint(news_review_identity(r.news))] for r in members},
            "sources": [source_item(r) for r in members]})
    items.sort(key=lambda item: (item["score"], item["heat"]), reverse=True)
    # 时间由 JSON 编码保存为 ISO；公开时仍重新核对引用是否可见、版本是否匹配。
    import json
    content = json.loads(json.dumps({"items": items[:12]}, ensure_ascii=False, default=str))
    issue = NewsEdition(community="ai", day=day, content=content, rule_version=EDITION_VERSION)
    session.add(issue)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        issue = await session.scalar(select(NewsEdition).where(NewsEdition.community == "ai", NewsEdition.day == day))
    return issue


async def read_news_edition(session, *, day=None):
    query = select(NewsEdition).where(NewsEdition.community == "ai")
    if day:
        query = query.where(NewsEdition.day == day)
    issue = await session.scalar(query.order_by(NewsEdition.day.desc()).limit(1))
    if issue is None:
        return {"day": None, "items": []}
    records = await visible_records(session, days=max(30, (utcnow().date() - issue.day).days + 31))
    versions = {str(r.raw.id): [r.raw.content_hash, r.version.version, fingerprint(news_review_identity(r.news))] for r in records}
    items = []
    for item in issue.content["items"]:
        refs = item["source_versions"]
        valid = {rid for rid, version in refs.items() if versions.get(rid) == version}
        if str(item["id"]) not in valid:
            continue  # 主稿撤回或修订时，同步撤回依赖它的刊期文案。
        items.append({k: v for k, v in item.items() if k != "source_versions"}
            | {"sources": [source for source in item["sources"] if str(source["id"]) in valid]})
    return {"day": issue.day, "created_at": issue.created_at, "items": items}


async def edition_bot_items(session, *, now=None, keywords=None):
    """邮件和机器人复用同一期已编排内容；没有当期刊物时允许原通道降级。"""
    from app.services.ai_news import load_bot_news

    now = now or utcnow()
    issue = await read_news_edition(session, day=(now + timedelta(hours=8)).date())
    if issue["day"] is None:
        return None
    pool = await load_bot_news(session, keywords, now - timedelta(days=30), now, collapse=False)
    by_id = {item["id"]: item for item in pool}
    result = []
    for entry in issue["items"]:
        if entry["id"] not in by_id:
            continue
        item = by_id[entry["id"]]
        result.append({**item, "title": entry["headline"], "summary": entry["summary"],
            "key_points": [entry["summary"]], "event_id": entry["event_ids"][0],
            "content_ids": [source["id"] for source in entry["sources"]], "edition_day": str(issue["day"])})
    return result
