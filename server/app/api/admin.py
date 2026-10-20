"""Admin API：采集目标、事件复核/编辑/锁定/合并/重算、商品 CRUD、分类词典 CRUD、内容浏览。"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.communities import DEFAULT_COMMUNITY, community_pattern
from app.api.deps import require_admin
from app.api.public import invalidate_official_links_cache
from app.config import get_settings
from app.db import get_session
from app.models import (
    ClassificationRule,
    CollectRun,
    CollectTarget,
    ContentReview,
    ContentVersion,
    Event,
    EventRevision,
    Evidence,
    MetricSnapshot,
    Platform,
    Product,
    RawContent,
    Source,
)
from app.models.collect_target import supports_view_threshold
from app.schemas.api import (
    validate_target_value,
    CollectTargetCreate,
    CollectTargetOut,
    CollectTargetUpdate,
    ContentItemOut,
    ContentListOut,
    EventListOut,
    EventMergeRequest,
    EventOut,
    EventSplitRequest,
    EventUpdate,
    OverviewOut,
    ProductCreate,
    ProductListOut,
    ProductOut,
    ProductSignalListOut,
    ProductSignalOut,
    ProductSignalUpdate,
    ProductUpdate,
    QueueStats,
    RuleCreate,
    RuleOut,
    RunTriggerOut,
)
from app.services.queue import ArqQueue, BaseQueue, LocalQueue
from app.services.views import events_to_out, product_to_out

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


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


# ---------- 采集目标 ----------


def _x_target_value(value: str, target_type: str, interval_minutes: int) -> str:
    from app.parsers.x import normalize_account

    if target_type != "account_dynamics":
        raise HTTPException(422, "X 目前仅支持账号动态监控")
    if interval_minutes < 30:
        raise HTTPException(422, "X 账号采集间隔至少 30 分钟")
    try:
        return normalize_account(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/collect-targets", response_model=list[CollectTargetOut])
async def list_targets(
    community: str | None = Query(default=None, pattern=community_pattern(), description="游戏分区"),
    session: AsyncSession = Depends(get_session),
):
    query = select(CollectTarget).order_by(CollectTarget.platform, CollectTarget.id)
    if community:
        query = query.where(CollectTarget.community == community)
    result = await session.execute(query)
    return list(result.scalars())


@router.post(
    "/collect-targets", response_model=CollectTargetOut, status_code=status.HTTP_201_CREATED
)
async def create_target(
    body: CollectTargetCreate, session: AsyncSession = Depends(get_session)
):
    from app.clock import utcnow

    if body.platform == "x":
        body.target_value = _x_target_value(body.target_value, body.target_type, body.interval_minutes)
    duplicate = await session.scalar(
        select(CollectTarget).where(
            CollectTarget.platform == body.platform,
            CollectTarget.target_type == body.target_type,
            CollectTarget.target_value == body.target_value,
        )
    )
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该采集目标已存在；不同分区请使用包含游戏名称的独立搜索词")
    min_views = body.min_views if "min_views" in body.model_fields_set else 10000
    if body.community == "ai" or not supports_view_threshold(body.platform, body.target_type):
        min_views = 0
    target = CollectTarget(
        platform=body.platform,
        target_type=body.target_type,
        target_value=body.target_value,
        display_name=body.display_name,
        interval_minutes=body.interval_minutes,
        enabled=body.enabled,
        co_terms=body.co_terms,
        min_views=min_views,
        tier=body.tier,
        community=body.community,
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
    if target.platform == "x":
        body.target_value = _x_target_value(body.target_value or target.target_value, target.target_type,
                                          body.interval_minutes if body.interval_minutes is not None else target.interval_minutes)
    bound_product = await session.scalar(select(Product).where(Product.monitor_target_id == target.id))
    if bound_product and body.community is not None and body.community != bound_product.community:
        raise HTTPException(status.HTTP_409_CONFLICT, "该目标已关联商品，请从商品管理修改所属分区")
    if body.display_name is not None:
        target.display_name = body.display_name
    if body.interval_minutes is not None:
        target.interval_minutes = body.interval_minutes
    if body.target_value is not None and body.target_value != target.target_value:
        # 目标类型不能改，格式按既有的 (platform, target_type) 组合判
        try:
            new_value = validate_target_value(
                target.platform, target.target_type, body.target_value
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
        duplicate = await session.scalar(
            select(CollectTarget).where(
                CollectTarget.platform == target.platform,
                CollectTarget.target_type == target.target_type,
                CollectTarget.target_value == new_value,
                CollectTarget.id != target.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "target already exists")
        target.target_value = new_value
        target.last_success_at, target.last_error, target.consecutive_failures = None, None, 0
        if target.enabled:
            target.next_run_at = utcnow()
    if body.tier is not None:
        target.tier = body.tier
    if body.community is not None:
        target.community = body.community
    if body.min_views is not None:
        target.min_views = body.min_views
    if target.community == "ai" or not supports_view_threshold(target.platform, target.target_type):
        target.min_views = 0
    if body.co_terms is not None:
        target.co_terms = body.co_terms
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


# ---------- 内容浏览 ----------


@router.get("/contents", response_model=ContentListOut)
async def list_contents(
    platform: str | None = Query(default=None),
    community: str | None = Query(default=None, pattern=community_pattern(), description="游戏分区"),
    hidden: bool | None = Query(default=None),
    review: str | None = Query(default=None, description="violation / irrelevant / ok"),
    q: str | None = Query(default=None, description="标题关键词"),
    tier: str | None = Query(default=None, description="core / general / none（来源分级）"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    conditions = []
    if platform:
        conditions.append(RawContent.platform == platform)
    if community:
        conditions.append(RawContent.community == community)
    if hidden is not None:
        conditions.append(RawContent.hidden.is_(hidden))
    if q:
        conditions.append(
            RawContent.id.in_(
                select(ContentVersion.raw_content_id).where(
                    ContentVersion.title.like(f"%{q}%")
                )
            )
        )
    if review == "violation":
        conditions.append(ContentReview.violation.is_(True))
    elif review == "irrelevant":
        conditions.append(ContentReview.relevant.is_(False))
    elif review == "ok":
        conditions.append(
            ContentReview.violation.is_(False) & ContentReview.relevant.is_(True)
        )

    core_terms: set[str] = set()
    core_rows = await session.scalars(
        select(CollectTarget).where(CollectTarget.tier == "core")
    )
    for target in core_rows:
        if target.target_type == "keyword_search" and target.target_value:
            core_terms.add(target.target_value)
        if target.display_name:
            core_terms.add(target.display_name)
        for term in target.co_terms or []:
            if term:
                core_terms.add(term)

    # 分级筛选时全量扫描后 Python 过滤分页（SQL offset 会漏掉窗口外的匹配行）
    rows = await session.execute(
        select(RawContent, Source.name)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .join(ContentReview, ContentReview.raw_content_id == RawContent.id, isouter=True)
        .where(*conditions)
        .order_by(RawContent.collected_at.desc(), RawContent.id.desc())
        .limit(2000 if tier is not None else limit)
        .offset(0 if tier is not None else offset)
    )
    all_items = []
    content_tiers: dict[int, str] = {}
    for raw, name in rows:
        keywords = raw.matched_keywords or []
        content_tier = (
            "core"
            if any(str(k) in core_terms for k in keywords)
            else "general"
            if keywords
            else "none"
        )
        if tier is not None and content_tier != tier:
            continue
        all_items.append((raw, name, content_tier))
        content_tiers[raw.id] = content_tier
    content_ids = [raw.id for raw, _, _ in all_items]

    reviews: dict[int, ContentReview] = {}
    if content_ids:
        review_rows = await session.execute(
            select(ContentReview).where(ContentReview.raw_content_id.in_(content_ids))
        )
        reviews = {row.raw_content_id: row for row in review_rows.scalars()}

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

    matched_keyword_map = {raw.id: (raw.matched_keywords or [])[:8] for raw, _, _ in all_items}
    items = [
        ContentItemOut(
            id=raw.id,
            platform=raw.platform,
            content_type=raw.content_type,
            external_id=raw.external_id,
            community=getattr(raw, "community", None) or DEFAULT_COMMUNITY,
            url=raw.url,
            published_at=raw.published_at,
            collected_at=raw.collected_at,
            source_name=name,
            title=latest_versions[raw.id].title if raw.id in latest_versions else None,
            version=latest_versions[raw.id].version if raw.id in latest_versions else None,
            latest_metrics=latest_metrics.get(raw.id),
            hidden=raw.hidden,
            tier=content_tiers[raw.id],
            matched_keywords=matched_keyword_map.get(raw.id, []),
            review=(
                {
                    "relevant": review.relevant,
                    "promotional": review.promotional,
                    "violation": review.violation,
                    "quality_score": review.quality_score,
                    "reason": review.reason,
                    "updated_at": review.updated_at,
                }
                if (review := reviews.get(raw.id))
                else None
            ),
        )
        for raw, name, content_tier in all_items
    ]

    if tier is not None:
        total = len(items)
        items = items[offset:offset + limit]
    else:
        total = await session.scalar(
            select(func.count(RawContent.id))
            .join(ContentReview, ContentReview.raw_content_id == RawContent.id, isouter=True)
            .where(*conditions)
        ) or 0
    return ContentListOut(items=items, total=total)


async def _get_content_or_404(session: AsyncSession, content_id: int) -> RawContent:
    raw = await session.get(RawContent, content_id)
    if raw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "content not found")
    return raw


@router.patch("/contents/{content_id}/visibility")
async def set_content_visibility(
    content_id: int,
    body: dict,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
):
    """人工下架/恢复：公开侧立即排除 hidden 内容，AI 不会再自动改动人工状态。"""
    raw = await _get_content_or_404(session, content_id)
    from app.services.fulfillment import capture_content_fulfillment, refresh_content_fulfillment

    snapshots = await capture_content_fulfillment(session, content_id)
    raw.hidden = bool(body.get("hidden"))
    if raw.community == "ai":
        from app.services.aihot_bridge import visibility
        await visibility(raw.id, raw.hidden)
    await refresh_content_fulfillment(session, snapshots, reaggregate=not raw.hidden)
    await session.commit()
    invalidate_official_links_cache()
    return {"id": raw.id, "hidden": raw.hidden}


@router.delete("/contents/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content(
    content_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
):
    """彻底删除内容（版本/指标/审核/证据随 FK 级联清除；事件本身保留）。"""
    raw = await _get_content_or_404(session, content_id)
    from app.services.fulfillment import capture_content_fulfillment, refresh_content_fulfillment

    snapshots = await capture_content_fulfillment(session, content_id)
    if raw.community == "ai":
        from app.services.aihot_bridge import visibility
        await visibility(raw.id, True)
    await session.delete(raw)
    await session.flush()
    await refresh_content_fulfillment(session, snapshots)
    await session.commit()
    invalidate_official_links_cache()


@router.get("/contents/{content_id}/detail")
async def content_edit_detail(
    content_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
):
    """编辑表单数据：最新版本的标题与正文。"""
    raw = await _get_content_or_404(session, content_id)
    version = await session.scalar(
        select(ContentVersion)
        .where(ContentVersion.raw_content_id == content_id)
        .order_by(ContentVersion.version.desc())
        .limit(1)
    )
    return {
        "id": raw.id,
        "hidden": raw.hidden,
        "title": version.title if version else None,
        "text": version.text if version else None,
        "version": version.version if version else None,
    }


@router.patch("/contents/{content_id}")
async def edit_content(
    content_id: int,
    body: dict,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
):
    """人工修订标题/正文：按设计原则产生新版本，不改写既有版本与原始数据。"""
    title = str(body.get("title") or "").strip() or None
    text = str(body.get("text") or "").strip() or None
    if title is None and text is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "标题与正文不能同时为空")
    raw = await _get_content_or_404(session, content_id)
    latest_version = await session.scalar(
        select(ContentVersion)
        .where(ContentVersion.raw_content_id == content_id)
        .order_by(ContentVersion.version.desc())
        .limit(1)
    )
    if latest_version is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该内容暂无可编辑的解析版本")
    from app.services.fulfillment import capture_content_fulfillment
    from app.services.events import run_aggregation

    snapshots = await capture_content_fulfillment(session, content_id)
    new_version = ContentVersion(
        raw_content_id=content_id,
        version=latest_version.version + 1,
        title=title if title is not None else latest_version.title,
        text=text if text is not None else latest_version.text,
        normalized_json=latest_version.normalized_json,
        parser_version=f"{latest_version.parser_version}+admin",
        category=latest_version.category,
    )
    session.add(new_version)
    await run_aggregation(session, product_snapshots=snapshots)
    await session.commit()
    invalidate_official_links_cache()
    return {"id": content_id, "version": new_version.version}


@router.post("/contents/{content_id}/review")
async def rereview_content(
    content_id: int,
    session: AsyncSession = Depends(get_session),
    queue=Depends(get_queue),
    _: None = Depends(require_admin),
):
    """手动重审：忽略既有判定，重新走多模态审核（结论会覆盖并重新计算下架状态）。"""
    await _get_content_or_404(session, content_id)
    await session.execute(
        ContentReview.__table__.delete().where(ContentReview.raw_content_id == content_id)
    )
    await session.commit()
    job_id = await queue.enqueue_review([content_id])
    if job_id is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "任务队列暂时不可用")
    return {"id": content_id, "status": "queued"}


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
            "items_fetched": run.items_fetched,
            "items_filtered": run.items_filtered,
            "parse_failed": run.parse_failed,
        }
        for run in result.scalars()
    ]


# ---------- 事件复核 ----------


@router.get("/events", response_model=EventListOut)
async def list_events(
    category: str | None = Query(default=None),
    community: str | None = Query(default=None, pattern=community_pattern(), description="游戏分区"),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    conditions = []
    if not include_inactive:
        conditions.append(Event.is_active.is_(True))
    if category:
        conditions.append(Event.category == category)
    if community:
        conditions.append(Event.community == community)
    total = await session.scalar(select(func.count(Event.id)).where(*conditions)) or 0
    events = list(
        (
            await session.execute(
                select(Event)
                .where(*conditions)
                .order_by(Event.last_updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
    )
    items = await events_to_out(session, events, include_detail=True)
    return EventListOut(items=items, total=total)


@router.put("/events/{event_id}", response_model=EventOut)
async def update_event(
    event_id: int,
    body: EventUpdate,
    session: AsyncSession = Depends(get_session),
):
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    from app.services.fulfillment import build_fulfillment_snapshot, sync_product_signals

    before = await build_fulfillment_snapshot(session, event.product_id) if event.product_id else None
    changes: list[str] = []
    for field, value in body.model_dump(exclude_unset=True).items():
        if getattr(event, field) != value:
            setattr(event, field, value)
            changes.append(field)
    if changes:
        if "summary" in changes:
            from app.clock import utcnow

            event.summary_status = "manual" if event.summary else None
            event.summary_updated_at = utcnow() if event.summary else None
        event.manual_locked = True  # 人工编辑后锁定，引擎不再覆盖（§12）
        session.add(
            EventRevision(
                event_id=event.id, actor="admin", note=f"人工编辑字段：{', '.join(changes)}"
            )
        )
        if event.product_id:
            await sync_product_signals(session, event.product_id, before=before)
    await session.commit()
    await session.refresh(event)
    return (await events_to_out(session, [event], include_detail=True))[0]


@router.post("/events/{event_id}/lock", response_model=EventOut)
async def set_lock(
    event_id: int,
    locked: bool = Query(...),
    session: AsyncSession = Depends(get_session),
):
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    event.manual_locked = locked
    session.add(
        EventRevision(
            event_id=event.id,
            actor="admin",
            note="人工锁定，引擎暂停改动" if locked else "解除锁定，恢复引擎维护",
        )
    )
    await session.commit()
    await session.refresh(event)
    return (await events_to_out(session, [event], include_detail=True))[0]


@router.post("/events/{event_id}/split", response_model=EventOut)
async def split_event_endpoint(
    event_id: int,
    body: EventSplitRequest,
    session: AsyncSession = Depends(get_session),
):
    from app.services.events import split_event

    try:
        event = await split_event(session, event_id, body.content_ids)
    except ValueError as exc:
        if str(exc) in {"event_not_found", "evidence_not_found"}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if event.category == "ai_news":
        from app.models import NewsGrouping
        from sqlalchemy import update
        await session.execute(update(NewsGrouping).where(NewsGrouping.raw_content_id.in_(body.content_ids))
            .values(event_id=event.id, story_id=event.id))
    await session.commit()
    await session.refresh(event)
    return (await events_to_out(session, [event], include_detail=True))[0]


@router.delete("/events/{event_id}/evidences/{content_id}")
async def remove_event_evidence(
    event_id: int,
    content_id: int,
    session: AsyncSession = Depends(get_session),
):
    from app.services.events import exclude_evidence

    try:
        remaining, deactivated = await exclude_evidence(session, event_id, content_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await session.commit()
    return {
        "removed": True,
        "remaining_evidences": remaining,
        "deactivated": deactivated,
    }


@router.post("/events/{event_id}/summarize")
async def regenerate_event_summary(
    event_id: int,
    session: AsyncSession = Depends(get_session),
):
    from app.services.ai import summarize_event

    result = await summarize_event(session, event_id, force=True)
    if result.get("status") == "failed":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.get("reason", "AI 摘要失败"))
    if result.get("status") == "skipped":
        code = (
            status.HTTP_404_NOT_FOUND
            if result.get("reason") == "event_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(code, result.get("reason", "摘要任务已跳过"))
    return result


@router.post("/events/merge")
async def merge_events(
    body: EventMergeRequest, session: AsyncSession = Depends(get_session)
):
    if body.source_id == body.target_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot merge into itself")
    source = await session.get(Event, body.source_id)
    target = await session.get(Event, body.target_id)
    if source is None or target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    if (source.category == "ai_news" or target.category == "ai_news") and (
        source.category != target.category or source.community != target.community
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "资讯事件只能在同一分区内合并")

    existing = set(
        await session.scalars(
            select(Evidence.raw_content_id).where(Evidence.event_id == target.id)
        )
    )
    moved = 0
    for evidence in list(
        (await session.execute(select(Evidence).where(Evidence.event_id == source.id))).scalars()
    ):
        if evidence.raw_content_id in existing:
            await session.delete(evidence)
            continue
        evidence.event_id = target.id
        moved += 1
    source.is_active = False
    source.manual_locked = True
    if source.category == "ai_news":
        from app.models import NewsGrouping
        from sqlalchemy import update
        target_story = await session.scalar(select(NewsGrouping.story_id).where(NewsGrouping.event_id == target.id).limit(1)) or target.id
        await session.execute(update(NewsGrouping).where(NewsGrouping.event_id == source.id)
            .values(event_id=target.id, story_id=target_story))
        await session.execute(update(NewsGrouping).where(NewsGrouping.story_id == source.id).values(story_id=target_story))
        target.manual_locked = True
    session.add(
        EventRevision(
            event_id=target.id,
            actor="admin",
            note=f"合并事件 #{source.id}（迁入 {moved} 条证据）",
        )
    )
    await session.commit()
    return {"merged": True, "moved_evidences": moved, "deactivated": source.id}


@router.post("/events/recompute")
async def recompute_events(session: AsyncSession = Depends(get_session)):
    from app.services.events import run_aggregation

    stats = await run_aggregation(session)
    await session.commit()
    return {
        "events_created": stats.events_created,
        "evidences_added": stats.evidences_added,
        "evidences_removed": stats.evidences_removed,
        "events_deactivated": stats.events_deactivated,
        "revisions_written": stats.revisions_written,
        "stage_advances": stats.stage_advances,
        "skipped_locked": stats.skipped_locked,
    }


# ---------- 商品 ----------


@router.get("/products", response_model=ProductListOut)
async def list_products_admin(
    community: str | None = Query(default=None, pattern=community_pattern(), description="游戏分区"),
    session: AsyncSession = Depends(get_session),
):
    query = select(Product).order_by(Product.id)
    if community:
        query = query.where(Product.community == community)
    products = list((await session.execute(query)).scalars())
    return ProductListOut(items=[await product_to_out(session, p) for p in products])


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(body: ProductCreate, session: AsyncSession = Depends(get_session)):
    from app.clock import utcnow
    from app.services.events import run_aggregation

    slug = (body.slug or "").strip() or f"product-{uuid.uuid4().hex[:8]}"
    duplicate = await session.scalar(select(Product).where(Product.slug == slug))
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "slug already exists")
    watch_terms = list(dict.fromkeys(t.strip() for t in body.watch_terms if t.strip()))
    if not watch_terms:
        watch_terms = [body.name.strip()]
    data = body.model_dump(
        exclude={"slug", "create_bilibili_target", "monitor_keyword", "monitor_enabled", "monitor_interval_minutes", "monitor_min_views"}
    )
    data["watch_terms"] = watch_terms
    product = Product(slug=slug, **data)
    session.add(product)
    await session.flush()

    from app.services.product_monitoring import configure_product_monitor

    try:
        await configure_product_monitor(
            session, product,
            enabled=body.monitor_enabled if body.monitor_enabled is not None else body.create_bilibili_target,
            keyword=body.monitor_keyword, interval_minutes=body.monitor_interval_minutes,
            min_views=body.monitor_min_views,
        )
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    # 新商品立即回看已入库内容；新监控位由调度器在下一轮执行。
    await run_aggregation(session)
    await session.commit()
    await session.refresh(product)
    return await product_to_out(session, product)


@router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int, body: ProductUpdate, session: AsyncSession = Depends(get_session)
):
    product = await session.get(Product, product_id, with_for_update=True)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    changes = body.model_dump(exclude_unset=True)
    legacy_stage_fields = {
        "stage_hint",
        "stage_hint_reason",
        "stage_hint_evidence_ids",
    }
    if legacy_stage_fields.intersection(changes):
        raise HTTPException(
            status.HTTP_410_GONE,
            "整段履约阶段覆盖已停用，请改为审核具体履约信号",
        )
    from app.services.fulfillment import build_fulfillment_snapshot
    from app.services.product_monitoring import configure_product_monitor

    before = await build_fulfillment_snapshot(session, product_id)
    monitor_changes = {key.removeprefix("monitor_"): changes.pop(key) for key in list(changes) if key.startswith("monitor_")}
    for field, value in changes.items():
        setattr(product, field, value)
    try:
        if monitor_changes or product.monitor_target_id:
            await configure_product_monitor(session, product, **monitor_changes)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if {"watch_terms", "community"}.intersection(changes):
        from app.services.events import run_aggregation

        product.watch_terms = list(
            dict.fromkeys(t.strip() for t in (product.watch_terms or []) if t.strip())
        )
        await session.flush()
        await run_aggregation(session, product_snapshots={product_id: before})
    await session.commit()
    await session.refresh(product)
    return await product_to_out(session, product)


@router.post("/products/{product_id}/assess", response_model=ProductOut)
async def assess_product(
    product_id: int, session: AsyncSession = Depends(get_session)
):
    raise HTTPException(
        status.HTTP_410_GONE,
        "AI 整体阶段判断已停用；模型输出只能进入候选信号，不能直接形成公开结论",
    )


@router.get(
    "/products/{product_id}/signals", response_model=ProductSignalListOut
)
async def list_product_signals_admin(
    product_id: int, session: AsyncSession = Depends(get_session)
):
    from app.services.fulfillment import list_product_signals

    if await session.get(Product, product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    items = await list_product_signals(session, product_id, include_rejected=True)
    return ProductSignalListOut(items=items, total=len(items))


@router.patch(
    "/products/{product_id}/signals/{signal_id}", response_model=ProductSignalOut
)
async def update_product_signal(
    product_id: int,
    signal_id: int,
    body: ProductSignalUpdate,
    session: AsyncSession = Depends(get_session),
):
    from app.services.fulfillment import list_product_signals, review_product_signal

    try:
        await review_product_signal(
            session,
            product_id,
            signal_id,
            **body.model_dump(),
        )
    except ValueError as exc:
        if str(exc) in {"signal_not_found", "signal_evidence_not_found"}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "signal not found") from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    items = await list_product_signals(session, product_id, include_rejected=True)
    return next(item for item in items if item["id"] == signal_id)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: int, session: AsyncSession = Depends(get_session)):
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    if product.monitor_target_id:
        target = await session.get(CollectTarget, product.monitor_target_id)
        if target:
            target.enabled = False
    for event in await session.scalars(select(Event).where(Event.product_id == product.id)):
        event.is_active = False
    await session.delete(product)
    await session.commit()


# ---------- 分类词典 ----------


@router.get("/classification-rules", response_model=list[RuleOut])
async def list_rules(session: AsyncSession = Depends(get_session)):
    rules = list(
        (
            await session.execute(
                select(ClassificationRule).order_by(
                    ClassificationRule.priority, ClassificationRule.id
                )
            )
        ).scalars()
    )
    return rules


@router.post(
    "/classification-rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED
)
async def create_rule(body: RuleCreate, session: AsyncSession = Depends(get_session)):
    rule = ClassificationRule(**body.model_dump())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.put("/classification-rules/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: int, body: RuleCreate, session: AsyncSession = Depends(get_session)
):
    rule = await session.get(ClassificationRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/classification-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: int, session: AsyncSession = Depends(get_session)):
    rule = await session.get(ClassificationRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    await session.delete(rule)
    await session.commit()


# ---------- 账号辅助 ----------


@router.get("/email-codes")
async def list_email_codes(
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """最近验证码（SMTP 未配置时的开发/排查通道；仅发信失败的码保留明文）。

    只列注册验证码，密码重置类一律不返回：重置码等于账号接管凭证，发给管理端
    就是一条绕过用户邮箱的取号路径。注册码的明文仍然保留——「没配 SMTP 的自托管
    实例」靠它完成首次注册，这是该端点原本的设计用途，砍掉等于把注册流程锁死。
    """
    from app.models import EmailCode, User

    rows = await session.execute(
        select(EmailCode, User.email)
        .join(User, EmailCode.user_id == User.id)
        .where(EmailCode.purpose != "reset")
        .order_by(EmailCode.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": code.id,
            "email": email,
            "purpose": code.purpose,
            # 明文只在「注册 + 发信失败」时给出；reset 类已在上面被整类过滤
            "code": code.code_plain,
            "attempts": code.attempts,
            "expires_at": code.expires_at,
            "used": code.used_at is not None,
            "sent_via_smtp": code.code_plain is None,
        }
        for code, email in rows
    ]


@router.get("/users/count")
async def users_count(session: AsyncSession = Depends(get_session)):
    from app.models import User

    total = await session.scalar(select(func.count(User.id))) or 0
    verified = await session.scalar(
        select(func.count(User.id)).where(User.is_verified.is_(True))
    ) or 0
    return {"total": total, "verified": verified}


# ---------- 重算入口 ----------


@router.post("/reparse")
async def reparse_all(
    platform: str | None = Query(default=None, description="缺省 = 全部平台"),
    content_type: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """parser/词典调整后的全量重算：重解析 + 重分类 + 事件重聚合。"""
    from app.services.events import run_aggregation
    from app.services.ingest import reparse_contents

    platforms = (
        [platform] if platform else [item.value for item in Platform]
    )
    summary = {"checked": 0, "updated": 0, "failed": 0}
    for name in platforms:
        result = await reparse_contents(session, name, content_type)
        for key in summary:
            summary[key] += result[key]
    stats = await run_aggregation(session)
    await session.commit()
    summary["stage_advances"] = stats.stage_advances
    return summary
