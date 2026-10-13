"""AI 快讯公开接口：按来源发布时间排序，筛选通过也仍是待核实的转述。"""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
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
    id: int
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
    news = ContentReview.news
    query = (await approved_news_query(session)).where(RawContent.published_at >= utcnow() - timedelta(hours=hours))
    hidden_platforms = settings_service.hidden_platforms(await settings_service.get_media_visibility(session))
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
    return NewsList(items=items, total=total, topics=list(NEWS_TOPICS), sources=sources)
