"""管理端 AI 专用：LLM 配置、连通测试、用量统计、最近调用、批量任务。"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import get_queue
from app.api.deps import require_admin
from app.clock import utcnow
from app.communities import community_pattern
from app.db import get_session
from app.models import AiUsage
from app.services import settings as settings_service
from app.services.ai import (
    _public_view,
    ai_available,
    chat,
    get_ai_config,
    record_usage,
    summarize_event,
    translate_content,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/ai", dependencies=[Depends(require_admin)])


@router.get("/config")
async def get_config(session: AsyncSession = Depends(get_session)):
    return _public_view(await get_ai_config(session))


@router.put("/config")
async def put_config(body: dict, session: AsyncSession = Depends(get_session)):
    stored = (await settings_service.get_setting(session, "ai")) or {}
    api_key = body.get("api_key")
    if api_key is None or api_key == "":
        api_key = stored.get("api_key") or ""
    raw_keys = body.get("api_keys")
    if raw_keys is None:
        api_keys = stored.get("api_keys") or []
    else:
        if isinstance(raw_keys, str):
            raw_keys = raw_keys.replace("，", ",").replace(",", "\n").split("\n")
        api_keys = [str(k).strip() for k in raw_keys if str(k).strip()]
    value = {
        "base_url": str(body.get("base_url") or "").strip().rstrip("/"),
        "api_key": api_key,
        "api_keys": api_keys,
        "model": str(body.get("model") or "").strip(),
        "price_in": float(body.get("price_in") or 0),
        "price_out": float(body.get("price_out") or 0),
    }
    await settings_service.set_setting(session, "ai", value)
    return _public_view(await get_ai_config(session))


@router.post("/test")
async def test_ai(session: AsyncSession = Depends(get_session)):
    import time

    config = await get_ai_config(session)
    if not ai_available(config):
        return {"ok": False, "message": "未配置完整（需要 base_url / api_key / model）"}
    started = time.monotonic()
    try:
        reply, usage = await chat(config, "你是连通性测试。", "回复两个字：正常")
        await record_usage(session, task="test", config=config, usage=usage,
                           duration_ms=int((time.monotonic() - started) * 1000))
        tokens = (usage or {}).get("total_tokens")
        return {"ok": True, "message": f"模型响应：{reply[:50]}（{tokens} tokens）"}
    except Exception as exc:  # noqa: BLE001
        await record_usage(session, task="test", config=config, usage=None,
                           duration_ms=int((time.monotonic() - started) * 1000), status="failed")
        return {"ok": False, "message": f"调用失败：{exc!r}"[:200]}


@router.get("/usage")
async def usage_stats(
    days: int = Query(default=7, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
):
    since = utcnow() - timedelta(days=days)
    rows = await session.execute(
        select(
            AiUsage.task,
            func.count(AiUsage.id),
            func.sum(AiUsage.prompt_tokens),
            func.sum(AiUsage.completion_tokens),
            func.sum(AiUsage.total_tokens),
            func.sum(AiUsage.cost),
            func.avg(AiUsage.duration_ms),
        )
        .where(AiUsage.created_at >= since)
        .group_by(AiUsage.task)
    )
    by_task = [
        {
            "task": task,
            "calls": calls,
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "total_tokens": int(total or 0),
            "cost": round(float(cost or 0), 4),
            "avg_ms": int(avg or 0),
        }
        for task, calls, prompt, completion, total, cost, avg in rows
    ]
    total_row = await session.execute(
        select(
            func.count(AiUsage.id),
            func.sum(AiUsage.total_tokens),
            func.sum(AiUsage.cost),
        ).where(AiUsage.created_at >= since)
    )
    calls, tokens, cost = total_row.first()
    # 全部历史
    all_row = await session.execute(
        select(func.count(AiUsage.id), func.sum(AiUsage.total_tokens), func.sum(AiUsage.cost))
    )
    a_calls, a_tokens, a_cost = all_row.first()
    return {
        "window_days": days,
        "window": {
            "calls": calls or 0,
            "total_tokens": int(tokens or 0),
            "cost": round(float(cost or 0), 4),
        },
        "all_time": {
            "calls": a_calls or 0,
            "total_tokens": int(a_tokens or 0),
            "cost": round(float(a_cost or 0), 4),
        },
        "by_task": by_task,
    }


@router.get("/calls")
async def recent_calls(
    limit: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(
        select(AiUsage).order_by(AiUsage.id.desc()).limit(limit)
    )
    return [
        {
            "id": r.id,
            "task": r.task,
            "ref_id": r.ref_id,
            "model": r.model,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "total_tokens": r.total_tokens,
            "cost": r.cost,
            "duration_ms": r.duration_ms,
            "status": r.status,
            "created_at": r.created_at,
        }
        for r in rows.scalars()
    ]


@router.post("/summarize-all")
async def summarize_all(
    only_missing: bool = True, session: AsyncSession = Depends(get_session)
):
    from app.models import Event

    query = select(Event.id).where(Event.is_active.is_(True))
    if only_missing:
        query = query.where(Event.summary.is_(None))
    ids = [row for row in await session.scalars(query)]
    done = skipped = failed = 0
    for event_id in ids[:200]:
        result = await summarize_event(session, event_id)
        s = result.get("status")
        if s == "ok":
            done += 1
        elif s == "skipped":
            skipped += 1
        else:
            failed += 1
    return {"attempted": len(ids), "ok": done, "skipped": skipped, "failed": failed}


@router.post("/review-recent")
async def review_recent(
    limit: int = Query(default=40, ge=1, le=80),
    only_missing: bool = True,
    session: AsyncSession = Depends(get_session),
):
    """对近期带首图内容执行 AI 相关性与精选评分。"""
    from app.services.curation import review_recent_contents

    stats = await review_recent_contents(session, limit=limit, only_missing=only_missing)
    return stats.__dict__


@router.post("/review-community")
async def review_community(
    community: str = Query(..., pattern=community_pattern(), description="游戏分区"),
    limit: int = Query(default=500, ge=1, le=1000, description="单次最多重审条数"),
    only_missing: bool = Query(default=False, description="只处理当前没有判定记录的内容（增量补审）"),
    session: AsyncSession = Depends(get_session),
    queue=Depends(get_queue),
):
    """按分区批量重审：清除该分区内容的既有 AI 判定并入队重新审核。

    用途：分区归属或审核提示词调整后的存量修正（如 blhx 内容曾按 wuwa 语境
    误判"与鸣潮无关"而下架）。重审会按新判定重新计算下架状态。
    免费模型限流下建议 only_missing=true 小批量多次调用做增量补审。
    """
    from app.models import ContentReview, RawContent

    query = select(RawContent.id).where(RawContent.community == community)
    if only_missing:
        has_review = select(ContentReview.raw_content_id).where(
            ContentReview.raw_content_id == RawContent.id
        ).exists()
        query = query.where(~has_review)
    ids = list(await session.scalars(query.order_by(RawContent.id).limit(limit)))
    if not ids:
        return {"community": community, "cleared": 0, "queued": 0}
    await session.execute(
        ContentReview.__table__.delete().where(ContentReview.raw_content_id.in_(ids))
    )
    await session.commit()
    queued = 0
    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        if await queue.enqueue_review(chunk) is not None:
            queued += len(chunk)
    if queued < len(ids):
        logger.warning("review-community: %s items failed to enqueue", len(ids) - queued)
    return {"community": community, "cleared": len(ids), "queued": queued}


@router.post("/translate-missing")
async def translate_missing(session: AsyncSession = Depends(get_session)):
    """为外语内容（国际服公告 / YouTube）补机翻。"""
    from app.models import ContentTranslation as CT
    from app.models import RawContent

    ids = [
        row
        for row in await session.scalars(
            select(RawContent.id)
            .where(RawContent.platform.in_(["kuro_global", "youtube"]))
            .order_by(RawContent.collected_at.desc())
            .limit(100)
        )
    ]
    existing = set(await session.scalars(select(CT.raw_content_id)))
    targets = [i for i in ids if i not in existing]
    done = skipped = failed = 0
    for content_id in targets[:50]:
        result = await translate_content(session, content_id)
        s = result.get("status")
        if s == "ok":
            done += 1
        elif s == "skipped":
            skipped += 1
        else:
            failed += 1
    return {"candidates": len(ids), "pending": len(targets), "ok": done, "skipped": skipped, "failed": failed}
