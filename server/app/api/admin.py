"""Admin API：采集目标 CRUD、手动触发、健康总览、内容浏览。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.config import get_settings
from app.db import get_session
from app.models import CollectRun, CollectTarget, ContentVersion, MetricSnapshot, RawContent, Source
from app.schemas.api import (
    CollectTargetCreate,
    CollectTargetOut,
    CollectTargetUpdate,
    ContentItemOut,
    ContentListOut,
    OverviewOut,
    QueueStats,
    RunTriggerOut,
)
from app.services.queue import ArqQueue, BaseQueue, LocalQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


async def get_queue(request: Request) -> BaseQueue:
    """任务队列：优先用 app.state 上已初始化的（本地模式由 lifespan 创建，
    测试由 fixture 注入）；否则按 local_mode 懒创建 arq 或本地实现。"""
    queue = getattr(request.app.state, "queue", None)
    if queue is None:
        if get_settings().local_mode:
            queue = LocalQueue()
        else:
            from app.services.collect import get_redis_pool

            queue = ArqQueue(await get_redis_pool())
        request.app.state.queue = queue
    return queue


@router.get("/collect-targets", response_model=list[CollectTargetOut])
async def list_targets(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(CollectTarget).order_by(CollectTarget.platform, CollectTarget.id)
    )
    return list(result.scalars())


@router.post(
    "/collect-targets", response_model=CollectTargetOut, status_code=status.HTTP_201_CREATED
)
async def create_target(
    body: CollectTargetCreate, session: AsyncSession = Depends(get_session)
):
    from app.clock import utcnow

    duplicate = await session.scalar(
        select(CollectTarget).where(
            CollectTarget.platform == body.platform,
            CollectTarget.target_type == body.target_type,
            CollectTarget.target_value == body.target_value,
        )
    )
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "target already exists")
    target = CollectTarget(
        platform=body.platform,
        target_type=body.target_type,
        target_value=body.target_value,
        display_name=body.display_name,
        interval_minutes=body.interval_minutes,
        enabled=body.enabled,
        next_run_at=utcnow() if body.enabled else None,
    )
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


@router.put("/collect-targets/{target_id}", response_model=CollectTargetOut)
async def update_target(
    target_id: int,
    body: CollectTargetUpdate,
    session: AsyncSession = Depends(get_session),
):
    from app.clock import utcnow

    target = await session.get(CollectTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "target not found")
    if body.display_name is not None:
        target.display_name = body.display_name
    if body.interval_minutes is not None:
        target.interval_minutes = body.interval_minutes
    if body.target_value is not None and body.target_value != target.target_value:
        duplicate = await session.scalar(
            select(CollectTarget).where(
                CollectTarget.platform == target.platform,
                CollectTarget.target_type == target.target_type,
                CollectTarget.target_value == body.target_value,
                CollectTarget.id != target.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "target already exists")
        target.target_value = body.target_value
    if body.enabled is not None:
        was_disabled = not target.enabled
        target.enabled = body.enabled
        if body.enabled and (was_disabled or target.next_run_at is None):
            target.next_run_at = utcnow()
    await session.commit()
    await session.refresh(target)
    return target


@router.delete("/collect-targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(target_id: int, session: AsyncSession = Depends(get_session)):
    target = await session.get(CollectTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "target not found")
    await session.delete(target)
    await session.commit()


@router.post("/collect-targets/{target_id}/run", response_model=RunTriggerOut)
async def trigger_run(
    target_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    queue: BaseQueue = Depends(get_queue),
):
    target = await session.get(CollectTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "target not found")
    job_id = await queue.enqueue_collect(target_id)
    return RunTriggerOut(job_enqueued=job_id is not None, job_id=job_id)


@router.get("/overview", response_model=OverviewOut)
async def overview(
    session: AsyncSession = Depends(get_session),
    queue: BaseQueue = Depends(get_queue),
):
    result = await session.execute(select(CollectTarget).order_by(CollectTarget.id))
    targets = list(result.scalars())
    total_contents = await session.scalar(select(func.count(RawContent.id)))
    pending = await queue.pending_count()
    return OverviewOut(
        targets=[CollectTargetOut.model_validate(t) for t in targets],
        queue=QueueStats(pending_jobs=pending),
        total_contents=total_contents or 0,
    )


@router.get("/contents", response_model=ContentListOut)
async def list_contents(
    platform: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    conditions = []
    if platform:
        conditions.append(RawContent.platform == platform)

    rows = await session.execute(
        select(RawContent, Source.name)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(*conditions)
        .order_by(RawContent.collected_at.desc(), RawContent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    page = [(raw, name) for raw, name in rows]
    content_ids = [raw.id for raw, _ in page]

    latest_versions: dict[int, ContentVersion] = {}
    latest_metrics: dict[int, dict] = {}
    if content_ids:
        version_rows = await session.execute(
            select(ContentVersion)
            .where(ContentVersion.raw_content_id.in_(content_ids))
            .order_by(ContentVersion.raw_content_id, ContentVersion.version.desc())
        )
        for version in version_rows.scalars():
            latest_versions.setdefault(version.raw_content_id, version)
        snapshot_rows = await session.execute(
            select(MetricSnapshot)
            .where(MetricSnapshot.raw_content_id.in_(content_ids))
            .order_by(MetricSnapshot.raw_content_id, MetricSnapshot.id.desc())
        )
        for snapshot in snapshot_rows.scalars():
            latest_metrics.setdefault(snapshot.raw_content_id, snapshot.metrics)

    items = [
        ContentItemOut(
            id=raw.id,
            platform=raw.platform,
            content_type=raw.content_type,
            external_id=raw.external_id,
            url=raw.url,
            published_at=raw.published_at,
            collected_at=raw.collected_at,
            source_name=name,
            title=latest_versions[raw.id].title if raw.id in latest_versions else None,
            version=latest_versions[raw.id].version if raw.id in latest_versions else None,
            latest_metrics=latest_metrics.get(raw.id),
        )
        for raw, name in page
    ]

    count_sq = select(func.count(RawContent.id)).where(*conditions)
    total = await session.scalar(count_sq) or 0
    return ContentListOut(items=items, total=total)


@router.get("/runs")
async def list_runs(
    target_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    query = select(CollectRun).order_by(CollectRun.started_at.desc()).limit(limit)
    if target_id is not None:
        query = query.where(CollectRun.target_id == target_id)
    result = await session.execute(query)
    return [
        {
            "id": run.id,
            "target_id": run.target_id,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "status": run.status,
            "error": run.error,
            "items_found": run.items_found,
            "items_new": run.items_new,
        }
        for run in result.scalars()
    ]
