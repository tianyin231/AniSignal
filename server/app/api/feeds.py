"""只读输出 API：带 token 的 RSS 2.0 feed（监控任务 / 事件）。

URL 形如 /api/feeds/target/12?token=xxx：token 是唯一凭据，可单独撤销/重建；
token 缺失返回 401，无效或已撤销统一返回 404（不泄露 feed 是否存在）。
条目只含标题/链接/时间/来源等公开字段，与站内公开内容同一口径。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services.feeds import (
    DEFAULT_FEED_LIMIT,
    MAX_FEED_LIMIT,
    feed_document,
    resolve_base_url,
)

router = APIRouter(prefix="/api/feeds")

MEDIA_TYPE = "application/rss+xml; charset=utf-8"


@router.get("/{kind}/{ref_id}", response_class=Response)
async def read_feed(
    kind: str,
    ref_id: int,
    request: Request,
    token: str = Query(default="", description="只读 token"),
    limit: int = Query(default=DEFAULT_FEED_LIMIT, ge=1, le=MAX_FEED_LIMIT),
    session: AsyncSession = Depends(get_session),
):
    if kind not in {"target", "event"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "feed 不存在")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少 token")
    document = await feed_document(
        session, kind, ref_id, token, resolve_base_url(request), limit=limit
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "feed 不存在或 token 无效")
    return Response(content=document, media_type=MEDIA_TYPE)
