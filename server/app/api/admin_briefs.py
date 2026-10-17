"""管理端每日情报简报 API（批次 3c）：历史简报列表、详情、试跑与手动推送。

路由前缀 `/api/admin/briefs`，鉴权走单管理员 Token。
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_session
from app.services import briefing

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/briefs", dependencies=[Depends(require_admin)])


def _coerce_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from exc


@router.get("")
async def list_briefs(
    session: AsyncSession = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    day: str | None = Query(default=None),
):
    """历史简报列表（最新在前）。"""
    return await briefing.list_briefs(
        session, page=page, page_size=page_size, day=_coerce_day(day)
    )


@router.get("/preview")
async def preview_brief(
    session: AsyncSession = Depends(get_session),
    day: str | None = Query(default=None),
):
    """试跑某一天的简报：算分节与摘要，不落库、不推送。"""
    return await briefing.preview_brief(session, _coerce_day(day))


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate_brief(
    session: AsyncSession = Depends(get_session),
    body: dict | None = None,
):
    """手动生成某一天的简报（已存在则原样返回，不重复推送）。"""
    day = _coerce_day((body or {}).get("day"))
    brief, created = await briefing.generate_brief(session, day, force=bool((body or {}).get("force")))
    return {"created": created, **briefing._view(brief)}


@router.post("/{brief_id}/send")
async def send_brief(brief_id: int, session: AsyncSession = Depends(get_session)):
    """手动推送一份简报（邮件 + QQ），结果写回投递记录。"""
    brief = await briefing.get_brief(session, brief_id)
    if brief is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "简报不存在")
    return {"id": brief.id, **await briefing.deliver_brief(session, brief)}


@router.get("/{brief_id}")
async def get_brief(brief_id: int, session: AsyncSession = Depends(get_session)):
    """简报详情：四个分节的结构化数据 + AI 摘要 + 投递结果。"""
    brief = await briefing.get_brief(session, brief_id)
    if brief is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "简报不存在")
    payload = briefing._view(brief)
    subject, body = briefing.render_brief_text(brief)
    payload["email_subject"] = subject
    payload["email_body"] = body
    return payload
