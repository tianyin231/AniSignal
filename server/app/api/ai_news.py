"""AI 快讯公开接口：按来源发布时间排序，筛选通过也仍是待核实的转述。"""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.db import get_session
from app.models import CollectTarget, ContentReview, ContentVersion, RawContent, Source
from app.services.ai_news import NEWS_TOPICS, approved_news_query, reference_links
from app.services import settings as settings_service

router = APIRouter(prefix="/api/ai-news")


class NewsItem(BaseModel):
    id: int | str
    path: str | None = None
    headline: str
    summary: str
    original_title: str
    kind: Literal["report", "rumor"]
    topics: list[str]
    platform: str
    source_name: str
    source_role: str
    verification: Literal["pending"] = "pending"
    published_at: datetime | None
    url: str | None
    references: list[dict]


class NewsSource(BaseModel):
    name: str
    platform: str
    status: str
    last_success_at: datetime | None
    interval_minutes: int


class NewsList(BaseModel):
    items: list[NewsItem]
    total: int
    topics: list[str]
    sources: list[NewsSource]


@router.get("", response_model=NewsList)
async def list_news(
    q: str = Query(default="", max_length=100),
    topic: str = Query(default="", max_length=30),
    kind: Literal["all", "report", "rumor"] = "all",
    hours: int = Query(default=168, ge=1, le=720),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    from app.services import aihot
    if aihot.enabled():
        return await aihot.feed(q=q, topic=topic, kind=kind, hours=hours, limit=limit, offset=offset)
    news = ContentReview.news
    query = (await approved_news_query(session)).where(RawContent.published_at >= utcnow() - timedelta(hours=hours))
    if kind != "all":
        query = query.where(news["kind"].as_string() == kind)
    if topic:
        # JSON 文本查询同时兼容 SQLite 开发库和 MySQL；词表限定可避免用户通配符。
        query = query.where(news["topics"].as_string().contains(f'"{topic}"', autoescape=True))
    if q.strip():
        query = query.where(or_(
            ContentVersion.title.icontains(q.strip(), autoescape=True),
            ContentVersion.text.icontains(q.strip(), autoescape=True),
            news["headline"].as_string().icontains(q.strip(), autoescape=True),
            Source.name.icontains(q.strip(), autoescape=True),
        ))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (await session.execute(query.order_by(
        RawContent.published_at.desc(), RawContent.id.desc(),
    ).limit(limit).offset(offset))).all()
    items = []
    for raw, version, source, review in rows:
        value = review.news
        items.append(NewsItem(
            id=raw.id, headline=value["headline"], summary=value["summary"],
            original_title=version.title or "", kind=value["kind"], topics=value["topics"],
            platform=raw.platform, source_name=source.name if source and source.name else "来源未署名",
            source_role="original" if raw.platform == "x" else "secondary", published_at=raw.published_at, url=raw.url,
            references=reference_links(version.text or ""),
        ))
    return NewsList(items=items, total=total, topics=list(NEWS_TOPICS), sources=await source_status(session))


async def source_status(session):
    hidden_platforms = settings_service.hidden_platforms(await settings_service.get_media_visibility(session))
    targets = list(await session.scalars(select(CollectTarget).where(
        CollectTarget.community == "ai", CollectTarget.enabled.is_(True),
    ).order_by(CollectTarget.id)))
    sources = []
    for target in targets:
        if target.platform in hidden_platforms:
            continue
        stale = target.last_success_at and target.last_success_at < utcnow() - timedelta(
            minutes=max(target.interval_minutes * 3, 90)
        )
        status = "delayed" if target.consecutive_failures or stale else (
            "active" if target.last_success_at else "pending"
        )
        sources.append(NewsSource(
            name=target.display_name or target.target_value, platform=target.platform,
            status=status, last_success_at=target.last_success_at,
            interval_minutes=target.interval_minutes,
        ))
    return sources


@router.get("/events")
async def news_events(
    q: str = Query(default="", max_length=100),
    topic: str = Query(default="", max_length=30),
    kind: Literal["all", "report", "rumor"] = "all",
    hours: int = Query(default=168, ge=1, le=720),
    sort: Literal["time", "hot"] = "time",
    selected: bool = False,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    from app.services import aihot
    if aihot.enabled():
        return await aihot.feed(q=q, topic=topic, kind=kind, hours=hours, sort=sort, selected=selected, limit=limit, offset=offset)
    from app.services.news_publication import list_stories

    items = await list_stories(session, q=q, topic=topic, kind=kind, hours=hours, sort=sort, selected=selected)
    return {"items": items[offset:offset + limit], "total": len(items), "topics": list(NEWS_TOPICS),
        "sources": await source_status(session)}


@router.get("/edition")
async def news_edition(day: date | None = None, session: AsyncSession = Depends(get_session)):
    from app.services import aihot
    if aihot.enabled():
        return await aihot.edition(day)
    from app.services.news_publication import read_news_edition

    return await read_news_edition(session, day=day)


@router.get("/rss.xml")
async def news_rss(session: AsyncSession = Depends(get_session)):
    from app.services import aihot
    if aihot.enabled():
        result = await aihot.request("/feed.xml", raw=True)
        from urllib.parse import urlsplit
        from xml.etree.ElementTree import fromstring
        parts = urlsplit(fromstring(result.content).findtext("channel/link") or "")
        origin = f"{parts.scheme}://{parts.netloc}"
        text = result.text.replace(origin + "/items/", origin + "/ai/items/").replace(origin + "/stories/", origin + "/ai/stories/")
        return Response(text, media_type="application/rss+xml", headers={"Cache-Control": "no-store"})
    from email.utils import format_datetime
    from datetime import timezone
    from xml.etree.ElementTree import Element, SubElement, tostring
    from app.config import get_settings
    from app.services.news_publication import list_stories

    base = get_settings().public_base_url.rstrip("/")
    root = Element("rss", version="2.0")
    channel = SubElement(root, "channel")
    SubElement(channel, "title").text = "AniSignal · AI 精选"
    SubElement(channel, "link").text = base + "/ai?mode=selected"
    SubElement(channel, "description").text = "AI 事件精选与后续进展，保留来源，报道仍待核实。"
    for entry in (await list_stories(session, selected=True))[:30]:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = entry["headline"]
        SubElement(item, "link").text = base + f"/contents/{entry['id']}?community=ai"
        SubElement(item, "guid", isPermaLink="false").text = f"news-event:{entry['event_id']}"
        SubElement(item, "pubDate").text = format_datetime(entry["latest_at"].replace(tzinfo=timezone.utc))
        SubElement(item, "description").text = entry["summary"] + "\n" + "\n".join(
            f"{source['source_name']}：{source['url'] or ''}" for source in entry["sources"])
    return Response(tostring(root, encoding="utf-8", xml_declaration=True), media_type="application/rss+xml",
        headers={"Cache-Control": "no-store"})


@router.get("/engine/{path:path}")
async def engine_public(path: str, request: Request):
    """只代理公开读接口；内部接口和上游管理端永不对外开放。"""
    import re
    from fastapi import HTTPException
    from app.services import aihot
    if not aihot.enabled():
        raise HTTPException(404)
    if not re.fullmatch(r"(?:items/[A-Za-z0-9_-]+(?:/original)?|stories/[A-Za-z0-9_-]+(?:/followups)?|groups/[A-Za-z0-9_-]+/reports|reports/(?:daily|weekly|monthly)(?:/[A-Za-z0-9_-]+)?|topics(?:/[A-Za-z0-9_-]+)?|hot)", path):
        raise HTTPException(404)
    result = await aihot.request("/api/site/" + path, params=dict(request.query_params))
    return result


@router.get("/media")
async def engine_media(request: Request):
    from fastapi import HTTPException
    from app.services import aihot
    if not aihot.enabled():
        raise HTTPException(404)
    result = await aihot.request("/api/img-proxy", params=dict(request.query_params), raw=True)
    return Response(result.content, media_type=result.headers.get("content-type", "image/webp"),
        headers={"Cache-Control": result.headers.get("cache-control", "private, max-age=60"),
                 "X-Content-Type-Options": "nosniff"})
