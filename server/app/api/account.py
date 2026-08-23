"""账号中心 API：关键词监控 CRUD、个性化关注流、账号概览。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db import get_session
from app.models import ContentVersion, RawContent, Source, User, UserKeyword
from app.schemas.api import PublicContentItem, PublicContentList

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", dependencies=[Depends(get_current_user)])

MAX_KEYWORDS = 10


@router.get("/overview")
async def overview(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """账号中心首屏：基础信息 + 订阅状态 + 关键词统计。"""
    keywords = list(
        (
            await session.execute(
                select(UserKeyword)
                .where(UserKeyword.user_id == user.id)
                .order_by(UserKeyword.id.desc())
            )
        ).scalars()
    )
    feed_total = 0
    if any(k.enabled for k in keywords):
        conditions = [
            ContentVersion.title.like(f"%{k.keyword}%")
            for k in keywords
            if k.enabled
        ]
        feed_total = (
            await session.scalar(
                select(func.count(func.distinct(RawContent.id)))
                .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
                .where(or_(*conditions))
            )
            or 0
        )
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar": user.avatar,
        "is_verified": user.is_verified,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "subscription": {"plan": "free", "label": "免费版", "expires_at": None},
        "keywords_total": len(keywords),
        "keywords_enabled": sum(1 for k in keywords if k.enabled),
        "feed_total": feed_total,
    }


@router.get("/keywords")
async def list_keywords(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    keywords = list(
        (
            await session.execute(
                select(UserKeyword)
                .where(UserKeyword.user_id == user.id)
                .order_by(UserKeyword.id.desc())
            )
        ).scalars()
    )
    return [
        {"id": k.id, "keyword": k.keyword, "enabled": k.enabled, "created_at": k.created_at}
        for k in keywords
    ]


@router.post("/keywords", status_code=status.HTTP_201_CREATED)
async def add_keyword(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    keyword = str(body.get("keyword", "")).strip()
    if not keyword or len(keyword) > 64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "关键词不能为空且不超过 64 字")
    count = await session.scalar(
        select(func.count(UserKeyword.id)).where(UserKeyword.user_id == user.id)
    )
    if (count or 0) >= MAX_KEYWORDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"最多 {MAX_KEYWORDS} 个关键词（基础订阅开放后可扩展）"
        )
    duplicate = await session.scalar(
        select(UserKeyword).where(
            UserKeyword.user_id == user.id, UserKeyword.keyword == keyword
        )
    )
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "关键词已存在")
    row = UserKeyword(user_id=user.id, keyword=keyword)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "keyword": row.keyword, "enabled": row.enabled, "created_at": row.created_at}


@router.put("/keywords/{keyword_id}")
async def toggle_keyword(
    keyword_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(UserKeyword, keyword_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "关键词不存在")
    if "enabled" in body:
        row.enabled = bool(body["enabled"])
    await session.commit()
    return {"id": row.id, "keyword": row.keyword, "enabled": row.enabled}


@router.delete("/keywords/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_keyword(
    keyword_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(UserKeyword, keyword_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "关键词不存在")
    await session.delete(row)
    await session.commit()


@router.get("/feed", response_model=PublicContentList)
async def my_feed(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """我的关注流：命中任一启用关键词的最新内容（标题匹配）。"""
    keywords = list(
        (
            await session.execute(
                select(UserKeyword).where(
                    UserKeyword.user_id == user.id, UserKeyword.enabled.is_(True)
                )
            )
        ).scalars()
    )
    if not keywords:
        return PublicContentList(items=[], total=0)

    conditions = [ContentVersion.title.like(f"%{k.keyword}%") for k in keywords]
    total = await session.scalar(
        select(func.count(func.distinct(RawContent.id)))
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .where(or_(*conditions))
    ) or 0

    rows = await session.execute(
        select(RawContent, Source, ContentVersion)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(or_(*conditions))
        .order_by(RawContent.collected_at.desc(), RawContent.id.desc(), ContentVersion.version.desc())
        .limit(limit * 3)
        .offset(offset)
    )
    items: list[PublicContentItem] = []
    seen: set[int] = set()
    for raw, source, version in rows:
        if raw.id in seen or len(items) >= limit:
            continue
        seen.add(raw.id)
        normalized = version.normalized_json or {}
        media = normalized.get("media") or []
        items.append(
            PublicContentItem(
                id=raw.id,
                platform=raw.platform,
                content_type=raw.content_type,
                external_id=raw.external_id,
                url=raw.url,
                title=version.title or raw.external_id,
                text_excerpt=_excerpt(version.text or str(normalized.get("text") or "")),
                published_at=raw.published_at,
                collected_at=raw.collected_at,
                source_name=(source.name if source else None),
                identity=(source.identity if source else "unknown"),
                metrics=None,
                cover=media[0] if isinstance(media, list) and media else None,
                preview_mode=(
                    "full"
                    if (version.text or str(normalized.get("text") or "")).strip()
                    else ("partial" if media else "external_only")
                ),
            )
        )
    return PublicContentList(items=items, total=total)


def _excerpt(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    if "<" in text and ">" in text:
        import bleach

        text = bleach.clean(text, tags=[], strip=True).strip()
    return text[:140] + ("…" if len(text) > 140 else "")
