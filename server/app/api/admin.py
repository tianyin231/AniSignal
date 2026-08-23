"""Admin API：采集目标、事件复核/编辑/锁定/合并/重算、商品 CRUD、分类词典 CRUD、内容浏览。"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.config import get_settings
from app.db import get_session
from app.models import (
    ClassificationRule,
    CollectRun,
    CollectTarget,
    ContentVersion,
    Event,
    EventRevision,
    Evidence,
    FULFILLMENT_STAGES,
    MetricSnapshot,
    Product,
    RawContent,
    Source,
)
from app.schemas.api import (
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
        co_terms=body.co_terms,
        min_views=body.min_views,
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


# ---------- 内容浏览 ----------


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


# ---------- 事件复核 ----------


@router.get("/events", response_model=EventListOut)
async def list_events(
    category: str | None = Query(default=None),
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
async def list_products_admin(session: AsyncSession = Depends(get_session)):
    products = list(
        (await session.execute(select(Product).order_by(Product.id))).scalars()
    )
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
        exclude={"slug", "create_bilibili_target", "monitor_keyword"}
    )
    data["watch_terms"] = watch_terms
    product = Product(slug=slug, **data)
    session.add(product)
    await session.flush()

    if body.create_bilibili_target:
        # 搜索词默认使用完整商品名，避免以角色名等宽泛匹配词采回大量无关内容。
        keyword = (body.monitor_keyword or "").strip() or body.name.strip()
        target = await session.scalar(
            select(CollectTarget).where(
                CollectTarget.platform == "bilibili",
                CollectTarget.target_type == "keyword_search",
                CollectTarget.target_value == keyword,
            )
        )
        if target is None:
            target = CollectTarget(
                platform="bilibili",
                target_type="keyword_search",
                target_value=keyword,
                display_name=f"B站搜索·{product.name}",
                interval_minutes=30,
                enabled=True,
                co_terms=[],
                min_views=0,
                next_run_at=utcnow(),
            )
            session.add(target)
        else:
            target.enabled = True
            target.next_run_at = target.next_run_at or utcnow()

    # 新商品立即回看已入库内容；新监控位由调度器在下一轮执行。
    await run_aggregation(session)
    await session.commit()
    await session.refresh(product)
    return await product_to_out(session, product)


@router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int, body: ProductUpdate, session: AsyncSession = Depends(get_session)
):
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    changes = body.model_dump(exclude_unset=True)
    stage = changes.pop("stage_hint", None)
    reason = changes.pop("stage_hint_reason", None)
    evidence_ids = changes.pop("stage_hint_evidence_ids", None)
    if stage is None and (reason is not None or evidence_ids is not None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "stage_hint is required")
    if stage is not None:
        from app.clock import utcnow

        previous_label = FULFILLMENT_STAGES[
            min(product.stage_hint if product.stage_hint >= 0 else product.computed_stage, 5)
        ]
        if stage == -1:
            product.stage_hint = -1
            product.stage_hint_source = None
            product.stage_hint_reason = None
            product.stage_hint_evidence_ids = []
            product.stage_hint_updated_at = utcnow()
        else:
            await _validate_stage_evidence(
                session, product.id, stage, reason or "", evidence_ids or []
            )
            product.stage_hint = stage
            product.stage_hint_source = "manual"
            product.stage_hint_reason = reason.strip()
            product.stage_hint_evidence_ids = list(dict.fromkeys(evidence_ids))
            product.stage_hint_updated_at = utcnow()
        effective_stage = product.computed_stage if stage == -1 else stage
        next_label = FULFILLMENT_STAGES[min(effective_stage, 5)]
        events = list(
            (
                await session.execute(
                    select(Event).where(
                        Event.product_id == product.id,
                        Event.is_active.is_(True),
                        Event.category.in_(["unboxing", "arrived"]),
                    )
                )
            ).scalars()
        )
        for event in events:
            event.status_label = next_label
            event.last_updated_at = utcnow()
            session.add(
                EventRevision(
                    event_id=event.id,
                    actor="admin",
                    note=f"履约阶段审核：{previous_label} → {next_label}",
                )
            )
    for field, value in changes.items():
        setattr(product, field, value)
    if "watch_terms" in changes:
        from app.services.events import run_aggregation

        product.watch_terms = list(
            dict.fromkeys(t.strip() for t in (product.watch_terms or []) if t.strip())
        )
        await session.flush()
        await run_aggregation(session)
    await session.commit()
    await session.refresh(product)
    return await product_to_out(session, product)


@router.post("/products/{product_id}/assess", response_model=ProductOut)
async def assess_product(
    product_id: int, session: AsyncSession = Depends(get_session)
):
    from app.services.ai import assess_product_stage

    result = await assess_product_stage(session, product_id)
    reason = result.get("reason")
    if result.get("status") != "ok":
        status_code = (
            status.HTTP_404_NOT_FOUND
            if reason == "product_not_found"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        messages = {
            "product_not_found": "product not found",
            "no_evidences": "暂无已关联的到货/开箱内容，请先完成精确采集",
            "ai_not_configured": "请先在 AI 层配置可用模型",
            "model_output_invalid": "模型返回未通过证据校验，请重试或人工复核",
        }
        raise HTTPException(status_code, messages.get(reason, str(reason or "AI 判断失败")))
    product = await session.get(Product, product_id)
    return await product_to_out(session, product)


async def _validate_stage_evidence(
    session: AsyncSession,
    product_id: int,
    stage: int,
    reason: str,
    evidence_ids: list[int],
) -> None:
    if stage == 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "不能用缺少证据证明未发货，请恢复自动状态",
        )
    if stage >= 4:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "阶段 4/5 需要购买时间样本，当前暂不可确认",
        )
    if not reason.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "请填写审核理由")
    selected = set(evidence_ids)
    if not selected:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "请选择原始内容证据")

    rows = list(
        await session.execute(
            select(Evidence, Event, RawContent, Source)
            .join(Event, Evidence.event_id == Event.id)
            .join(RawContent, Evidence.raw_content_id == RawContent.id)
            .join(Source, RawContent.source_id == Source.id, isouter=True)
            .where(
                Event.product_id == product_id,
                Event.is_active.is_(True),
                Event.category.in_(["unboxing", "arrived"]),
                Evidence.role != "excluded",
                RawContent.id.in_(selected),
            )
        )
    )
    found = {raw.id for _, _, raw, _ in rows}
    if found != selected:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "所选证据不属于该商品的到货/开箱事件")
    if stage >= 2:
        accounts = {
            source.external_id if source else f"{raw.platform}:{raw.external_id}"
            for _, _, raw, source in rows
            if (source.identity if source else "unknown") in ("user", "unknown")
        }
        required = 3 if stage == 3 else 1
        if len(accounts) < required:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"该阶段至少需要 {required} 个独立普通账号证据",
            )


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: int, session: AsyncSession = Depends(get_session)):
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
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
    """最近验证码（SMTP 未配置时的开发/排查通道；仅发信失败的码保留明文）。"""
    from app.models import EmailCode, User

    rows = await session.execute(
        select(EmailCode, User.email)
        .join(User, EmailCode.user_id == User.id)
        .order_by(EmailCode.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": code.id,
            "email": email,
            "purpose": code.purpose,
            "code": code.code_plain,
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
        [platform] if platform else ["bilibili", "miyoushe", "kuro_cn", "kuro_global", "youtube"]
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
