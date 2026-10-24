"""统一资讯读取：仅访问主数据库，页面和机器人共享发布判断。"""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.clock import utcnow
from app.db import SessionLocal
from app.models import ContentVersion, NewsArchive
from app.services.news_editorial import load_news_records
from app.services.news_import import date
from app.services.news_publication import list_stories, selected_score, source_item
from app.services.editorial_calls import get_policy


def publication_time(row):
    return row.raw.published_at or row.raw.collected_at


def available(row, now=None, *, listed=True):
    now = now or utcnow()
    imported = row.news.get("imported_publication") or {}
    release = date(imported.get("visible_after"))
    mode = (row.source.raw_json or {}).get("participation_mode", "editorial") if row.source else "editorial"
    return (mode == "editorial" and (not listed or imported.get("eligible", True))
        and not (row.current and row.event and not row.event.is_active)
        and (not imported.get("selected") or bool(release and release <= now)))


async def article_aliases(session, ids):
    if not ids:
        return {}
    values = await session.scalars(select(NewsArchive).where(NewsArchive.kind == "article",
        NewsArchive.raw_content_id.in_(ids)).order_by(NewsArchive.key))
    result = {}
    for value in values:
        result.setdefault(value.raw_content_id, value)
    return result


def article_id(row, aliases):
    alias = aliases.get(row.raw.id)
    return alias.key if alias else f"content-{row.raw.id}"


def revision(row, aliases):
    alias = aliases.get(row.raw.id)
    if alias:
        original = alias.payload.get("publication") or alias.payload.get("article") or {}
        return int(original.get("revision") or 1) + max(0, row.version.version - alias.payload.get("content_version", 1))
    return row.version.version


async def images(session, rows):
    ids = [row.version.id for row in rows]
    if not ids:
        return {}
    result = {}
    for rid, value in await session.execute(select(ContentVersion.raw_content_id, ContentVersion.normalized_json)
            .where(ContentVersion.id.in_(ids))):
        media = (value or {}).get("media") or []
        result[rid] = next((item if isinstance(item, str) else item.get("url") for item in media
            if isinstance(item, str) or isinstance(item, dict) and item.get("kind") == "image"), None)
    return result


async def feed(*, q="", topic="", kind="all", hours=168, sort="time", selected=False, limit=20, offset=0):
    async with SessionLocal() as session:
        if sort == "hot":
            entries = await list_stories(session, q=q, topic=topic, kind=kind, hours=hours, sort=sort, selected=selected)
            aliases = await article_aliases(session, [entry["id"] for entry in entries[offset:offset + limit]])
            items = []
            for entry in entries[offset:offset + limit]:
                alias = aliases.get(entry["id"])
                key = alias.key if alias else f"content-{entry['id']}"
                items.append({**entry, "id": "aihot:" + key, "path": "/ai/items/" + key, "engine": "anisignal"})
            return {"items": items, "total": len(entries), "topics": await topics(session), "sources": [], "engine": "anisignal"}
        now = utcnow()
        records = await load_news_records(session, since=now - timedelta(hours=hours), until=now + timedelta(microseconds=1))
        policy = await get_policy(session)
        rows = []
        for row in reversed(records):
            score, chosen = selected_score(row, policy)
            if not available(row, now) or selected and not chosen or kind != "all" and row.news["kind"] != kind:
                continue
            if topic and topic not in row.news["topics"]:
                continue
            if q and q.casefold() not in " ".join((row.news["headline"], row.news["summary"], row.version.title or "",
                    row.version.text or "", row.source.name if row.source and row.source.name else "")).casefold():
                continue
            rows.append((row, score, chosen))
        page = rows[offset:offset + limit]
        aliases = await article_aliases(session, [row.raw.id for row, _, _ in page])
        media = await images(session, [row for row, _, _ in page])
        items = []
        for row, score, chosen in page:
            key = article_id(row, aliases)
            items.append({**source_item(row), "id": "aihot:" + key, "path": "/ai/items/" + key,
                "engine": "anisignal", "score": score, "selected": chosen, "image": media.get(row.raw.id),
                "sources": [], "developments": [], "report_count": 1, "source_count": 1, "participant_count": 1,
                "latest_at": publication_time(row), "updated_at": row.review.updated_at})
        return {"items": items, "total": len(rows), "topics": await topics(session), "sources": [], "engine": "anisignal"}


async def topics(session):
    from app.services.ai_news import NEWS_TOPICS
    return list(NEWS_TOPICS)


async def sources():
    async with SessionLocal() as session:
        from app.api.ai_news import source_status
        return [row.model_dump() for row in await source_status(session)]


async def bot_items(keywords, start_at, end_at, *, collected_after=None):
    async with SessionLocal() as session:
        records = await load_news_records(session, since=start_at, until=end_at)
        policy = await get_policy(session)
        selected = []
        for row in reversed(records):
            imported = row.news.get("imported_publication") or {}
            ready = max(row.review.updated_at, date((row.news.get("selection") or {}).get("scored_at")) or row.review.updated_at)
            if not available(row, end_at) or not selected_score(row, policy)[1] or imported.get("seat") is False:
                continue
            if ready >= end_at or collected_after and (ready <= collected_after or imported.get("backfill")):
                continue
            text = " ".join((row.version.title or "", row.version.text or "", row.news["headline"], row.news["summary"]))
            if keywords and not any(k.casefold() in text.casefold() for k in keywords):
                continue
            selected.append(row)
        selected = selected[:200]
        aliases = await article_aliases(session, [row.raw.id for row in selected])
        output, seen = [], set()
        for row in selected:
            key = article_id(row, aliases)
            imported = row.news.get("imported_publication") or {}
            fact_id = imported.get("fact_id") or (f"event-{row.grouping.event_id}" if row.current and row.grouping.event_id else None)
            identity = fact_id or key
            if identity in seen:
                continue
            seen.add(identity)
            news = {**row.news, "engine": "aihot", "engine_id": key, "revision": revision(row, aliases),
                "fact_id": fact_id, "allow_backfill": collected_after is None}
            output.append({"id": "aihot:" + key, "community": "ai", "title": row.news["headline"],
                "source_title": row.version.title or "", "text": row.version.text or "", "summary": row.news["summary"],
                "key_points": [row.news["summary"]], "news": news, "url": row.raw.url, "image": None,
                "platform": row.raw.platform, "content_type": row.raw.content_type,
                "source": row.source.name if row.source else "来源未署名", "identity": row.source.identity if row.source else "unknown",
                "published_at": row.raw.published_at, "collected_at": row.raw.collected_at,
                "keywords": keywords or [], "strength": "strong", "category": "ai_news", "event_id": None,
                "importance": False, "content_ids": ["aihot:" + key]})
        return output


async def validate_bot_items(items):
    from app.services.news_details import resolve_article
    async with SessionLocal() as session:
        result = set()
        policy = await get_policy(session)
        for item in items:
            key = item["news"]["engine_id"]
            try:
                row, alias = await resolve_article(session, key, require_news=True)
            except HTTPException:
                continue
            if not available(row) or not selected_score(row, policy)[1]:
                continue
            aliases = {row.raw.id: alias} if alias else {}
            imported = row.news.get("imported_publication") or {}
            if imported.get("seat") is False or imported.get("backfill") and not item["news"].get("allow_backfill"):
                continue
            if revision(row, aliases) == item["news"]["revision"]:
                if all(not item["news"].get(field) or item["news"][field] == row.news.get(field)
                        for field in ["headline", "summary", "kind"]):
                    result.add("aihot:" + key)
        return result


async def request(path, *, method="GET", params=None, body=None, raw=False, missing_ok=False):
    from app.services import news_details
    async with SessionLocal() as session:
        try:
            return await news_details.request(session, path, method=method, params=params or {}, body=body or {}, raw=raw)
        except HTTPException as exc:
            if exc.status_code == 404 and missing_ok:
                return None
            raise
