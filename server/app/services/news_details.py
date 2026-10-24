"""统一资讯的文章、事件与旧地址兼容读取。"""

from collections import defaultdict
from html import escape
import re

from fastapi import HTTPException
import httpx
from sqlalchemy import or_, select

from app.clock import utcnow
from app.models import ContentTranslation, ContentVersion, NewsArchive, NewsGrouping
from app.services.news_editorial import load_news_records, representative
from app.services.editorial_calls import get_policy
from app.services.news_publication import participant, selected_score
from app.services import native_news


async def resolve_article(session, key, *, require_news=False):
    alias = await session.get(NewsArchive, ("article", key))
    match = re.fullmatch(r"(?:as|content)-(\d+)", key)
    rid = alias.raw_content_id if alias else int(match[1]) if match else None
    if rid is None:
        raise HTTPException(404, "资讯不存在")
    rows = await load_news_records(session, content_ids=[rid], include_archived=not require_news)
    if not rows or not native_news.available(rows[0], listed=require_news):
        raise HTTPException(404, "资讯已撤下或尚未发布")
    return rows[0], alias


async def article(session, key, *, original=False):
    row, alias = await resolve_article(session, key)
    policy = await get_policy(session)
    selected = selected_score(row, policy)[1]
    archived = (alias.payload.get("article") or {}) if alias else {}
    publication = (alias.payload.get("publication") or {}) if alias else {}
    current_archive = bool(alias and row.raw.content_hash == alias.payload.get("content_hash")
        and row.version.version == alias.payload.get("content_version"))
    body = None
    translation = await session.get(ContentTranslation, row.raw.id)
    mode = publication.get("body_mode") or "summary-only"
    if row.raw.platform == "x":
        mode = "original"
        body = {"original": "<p>" + escape(row.version.text or "").replace("\n", "<br>") + "</p>",
            "zh": None if original or not translation else "<p>" + escape(translation.text).replace("\n", "<br>") + "</p>",
            "complete": bool(not current_archive or archived.get("body_status") == "ok")}
    elif current_archive and mode not in {"summary-only", "none"} and archived.get("body_html"):
        from app.api.public import _sanitize_html
        body = {"zh": None, "original": _sanitize_html(archived["body_html"]), "complete": archived.get("body_status") == "ok"}
    story = None
    if row.current and row.grouping.story_id:
        story_alias = await session.scalar(select(NewsArchive).where(NewsArchive.kind == "story",
            NewsArchive.event_id == row.grouping.story_id).order_by(NewsArchive.key).limit(1))
        if story_alias:
            story = {"publicId": story_alias.payload["public_id"], "title": story_alias.payload["title"]}
        else:
            story = {"publicId": f"event-{row.grouping.story_id}", "title": row.event.title}
    fact = (row.news.get("imported_publication") or {}).get("fact_id")
    fact = str(fact) if fact is not None else f"event-{row.grouping.event_id}" if row.current and row.grouping.event_id else None
    return {"id": key, "title": row.news["headline"], "originalTitle": row.version.title,
        "summary": row.news["summary"], "reason": (publication.get("reason") if current_archive else row.review.reason),
        "source": {"name": row.source.name if row.source else "来源未署名"}, "links": {"original": row.raw.url},
        "publishedAt": row.raw.published_at, "selected": selected, "readingMode": mode, "body": body,
        "hasTranslation": bool(translation), "group": {"factId": fact, "additionalSourceCount": 0} if fact else None,
        "story": story, "relatedStories": []}


async def report_item(session, row, aliases):
    key = native_news.article_id(row, aliases)
    return {"id": key, "title": row.news["headline"], "summary": row.news["summary"],
        "source": {"name": row.source.name if row.source else "来源未署名"},
        "publishedAt": row.raw.published_at, "originalUrl": row.raw.url}


async def group_reports(session, key):
    alias = await session.get(NewsArchive, ("fact", key))
    match = re.fullmatch(r"event-(\d+)", key)
    eid = alias.event_id if alias else int(match[1]) if match else None
    if eid is None:
        raise HTTPException(404)
    ids = list(await session.scalars(select(NewsGrouping.raw_content_id).where(NewsGrouping.event_id == eid)))
    rows = [row for row in await load_news_records(session, content_ids=ids, include_archived=True)
        if native_news.available(row, listed=False)] if ids else []
    aliases = await native_news.article_aliases(session, ids)
    return {"reports": [await report_item(session, row, aliases) for row in rows]}


async def story(session, key):
    alias = await session.get(NewsArchive, ("story", key))
    match = re.fullmatch(r"event-(\d+)", key)
    root = alias.event_id if alias else int(match[1]) if match else None
    if root is None:
        raise HTTPException(404, "事件不存在")
    ids = list(await session.scalars(select(NewsGrouping.raw_content_id).where(or_(
        NewsGrouping.story_id == root, NewsGrouping.event_id == root))))
    rows = [row for row in await load_news_records(session, content_ids=ids, include_archived=True)
        if native_news.available(row, listed=False)] if ids else []
    if not rows:
        raise HTTPException(404, "事件已撤下")
    aliases = await native_news.article_aliases(session, ids)
    groups = defaultdict(list)
    for row in rows:
        groups[row.grouping.event_id].append(row)
    developments = []
    for eid, members in groups.items():
        rep = representative(members)
        fact = await session.scalar(select(NewsArchive).where(NewsArchive.kind == "fact", NewsArchive.event_id == eid).limit(1))
        developments.append({"factId": (fact.payload.get("public_id") or fact.key) if fact else f"event-{eid}",
            "title": rep.event.title if rep.event and rep.event.manual_locked else rep.news["headline"],
            "firstReportAt": min(native_news.publication_time(r) for r in members),
            "reportCount": len(members), "representative": await report_item(session, rep, aliases)})
    developments.sort(key=lambda item: item["firstReportAt"], reverse=True)
    native = alias.payload if alias else {}
    unchanged = len(rows) == len(ids) and all(not aliases.get(r.raw.id) or aliases[r.raw.id].payload.get("content_hash") == r.raw.content_hash for r in rows)
    return {"publicId": native.get("public_id") or key, "title": (native.get("title") if unchanged else None) or developments[0]["title"],
        "digest": native.get("digest") if unchanged else None, "summary": native.get("summary") if unchanged else None,
        "latest": native.get("latest") if unchanged else rows[-1].news["summary"],
        "sourceCount": len({participant(row) for row in rows}), "reportCount": len(rows),
        "latestAt": max(native_news.publication_time(row) for row in rows), "status": "active",
        "developments": developments, "timeline": [await report_item(session, row, aliases) for row in reversed(rows)],
        "related": []}


async def request(session, path, *, method="GET", params=None, body=None, raw=False):
    params = params or {}
    if method != "GET":
        raise HTTPException(405)
    match = re.fullmatch(r"/api/site/items/([A-Za-z0-9_-]+)(/original)?", path)
    if match:
        return await article(session, match[1], original=bool(match[2]))
    match = re.fullmatch(r"/api/site/stories/([A-Za-z0-9_-]+)(/followups)?", path)
    if match:
        result = await story(session, match[1])
        return {"items": result["developments"]} if match[2] else result
    match = re.fullmatch(r"/api/site/groups/([A-Za-z0-9_-]+)/reports", path)
    if match:
        return await group_reports(session, match[1])
    if path.startswith("/api/site/reports/"):
        from app.services.news_reports import request as read_reports
        return await read_reports(session, path.removeprefix("/api/site/reports/"))
    if path in {"/api/site/topics", "/api/site/hot"}:
        if path.endswith("topics"):
            return {"items": [{"name": name, "slug": name} for name in await native_news.topics(session)]}
        return {"entries": [], "computedAt": utcnow()}
    if path == "/feed.xml":
        from app.services.news_reports import rss
        return httpx.Response(200, content=await rss(), headers={"content-type": "application/rss+xml"})
    if path == "/api/img-proxy":
        from app.services.news_media import image
        return await image(session, params)
    raise HTTPException(404)
