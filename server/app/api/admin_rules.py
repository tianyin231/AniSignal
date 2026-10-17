"""管理端规则引擎 API：规则 CRUD、命中台账查询、声量基线查看与阈值配置。

路由前缀统一 `/api/admin/rules-engine`，鉴权走单管理员 Token（require_admin）。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.deps import require_admin
from app.models import Rule, RuleHit, VolumeBaseline
from app.schemas.api import (
    RuleEngineConfigUpdate,
    RuleEngineCreate,
    RuleEngineListOut,
    RuleEngineOut,
    RuleEngineUpdate,
    RuleHitListOut,
    RuleHitOut,
)
from app.services import boolean_query
from app.services import source_tier as source_tier_module
from app.services import settings as settings_service
from app.services import volume as volume_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/rules-engine", dependencies=[Depends(require_admin)])


def _rule_view(rule: Rule) -> dict:
    view = RuleEngineOut.model_validate(rule).model_dump()
    try:
        view["parsed_expr"] = boolean_query.describe(boolean_query.parse(rule.keyword_expr))
    except Exception as exc:  # noqa: BLE001 - 展示层解析失败不影响返回
        logger.warning("rule %s expr preview failed: %r", rule.id, exc)
        view["parsed_expr"] = "表达式无法解析"
    return view


async def _rule_or_404(session: AsyncSession, rule_id: int) -> Rule:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "规则不存在")
    return rule


@router.get("")
async def list_rules(
    session: AsyncSession = Depends(get_session),
    q: str = Query(default="", max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """规则列表（按 priority、id 升序，即实际执行顺序）。"""
    conditions = []
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        conditions.append(Rule.name.like(like) | Rule.keyword_expr.like(like))
    total = (
        await session.scalar(select(func.count()).select_from(Rule).where(*conditions))
    ) or 0
    rows = list(
        (
            await session.execute(
                select(Rule)
                .where(*conditions)
                .order_by(Rule.priority.asc(), Rule.id.asc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
    )
    return RuleEngineListOut(
        items=[RuleEngineOut(**_rule_view(row)) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: RuleEngineCreate, session: AsyncSession = Depends(get_session)
) -> dict:
    rule = Rule(
        name=body.name,
        enabled=body.enabled,
        priority=body.priority,
        keyword_expr=(body.keyword_expr or "").strip() or None,
        platforms=body.platforms,
        communities=body.communities,
        intent_tiers=body.intent_tiers,
        actions=[action.model_dump() for action in body.actions],
        note=body.note,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _rule_view(rule)


@router.post("/source-tiers/refresh")
async def refresh_source_tiers(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=2000, ge=1, le=20000),
):
    """按当前粉丝量与阈值重算全部来源档位（管理端改阈值后手动跑一次）。"""
    from app.models import Source

    ids = list(
        (await session.execute(select(Source.id).order_by(Source.id).limit(limit))).scalars()
    )
    cfg = await settings_service.get_rules_config(session)
    changed = await source_tier_module.refresh_source_tiers(
        session, ids, cfg.get("source_tiers")
    )
    distribution = dict(
        (
            await session.execute(
                select(Source.tier, func.count()).group_by(Source.tier)
            )
        ).all()
    )
    return {
        "scanned": len(ids),
        "changed": len(changed),
        "by_tier": _tally(changed.values()),
        "distribution": {str(key): int(value) for key, value in distribution.items()},
    }


def _tally(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


@router.post("/preview")
async def preview_rule(
    body: RuleEngineCreate,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """用最近的内容试跑条件（不执行动作），返回命中数与样例。"""
    from app.models import RawContent
    from app.services.rules import _condition_reason, _load_contexts

    ids = list(
        (
            await session.execute(
                select(RawContent.id)
                .where(RawContent.hidden.is_(False))
                .order_by(RawContent.collected_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    contexts = await _load_contexts(session, ids)
    parsed = boolean_query.parse((body.keyword_expr or "").strip() or None)
    stub = Rule(
        name=body.name,
        platforms=body.platforms,
        communities=body.communities,
        intent_tiers=body.intent_tiers,
    )
    hits = []
    for ctx in contexts:
        matched, reason = _condition_reason(stub, ctx, parsed)
        if matched:
            hits.append(
                {
                    "content_id": ctx.content.id,
                    "title": ctx.title,
                    "platform": str(ctx.content.platform),
                    "intent_tier": ctx.intent_tier or None,
                    "reason": reason,
                }
            )
    return {
        "scanned": len(contexts),
        "matched": len(hits),
        "parsed_expr": boolean_query.describe(parsed),
        "items": hits[:20],
    }


@router.get("/hits")
async def list_rule_hits(
    session: AsyncSession = Depends(get_session),
    rule_id: int | None = Query(default=None),
    content_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """规则命中台账：哪条规则处理了哪条内容、做了什么。"""
    from app.models import ContentVersion, RawContent

    conditions = []
    if rule_id is not None:
        conditions.append(RuleHit.rule_id == rule_id)
    if content_id is not None:
        conditions.append(RuleHit.content_id == content_id)
    total = (
        await session.scalar(
            select(func.count()).select_from(RuleHit).where(*conditions)
        )
    ) or 0
    rows = list(
        (
            await session.execute(
                select(RuleHit, Rule.name, RawContent.external_id)
                .join(Rule, Rule.id == RuleHit.rule_id)
                .join(RawContent, RawContent.id == RuleHit.content_id, isouter=True)
                .where(*conditions)
                .order_by(RuleHit.created_at.desc(), RuleHit.id.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
    )
    latest_sq = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    titles = dict(
        (
            await session.execute(
                select(ContentVersion.raw_content_id, ContentVersion.title)
                .join(
                    latest_sq,
                    (ContentVersion.raw_content_id == latest_sq.c.rid)
                    & (ContentVersion.version == latest_sq.c.version),
                )
                .where(ContentVersion.raw_content_id.in_([row[0].content_id for row in rows]))
            )
        ).all()
    ) if rows else {}
    items = []
    for hit, rule_name, _external in rows:
        view = RuleHitOut.model_validate(hit).model_dump()
        view["rule_name"] = rule_name
        view["content_title"] = titles.get(hit.content_id)
        items.append(RuleHitOut(**view))
    return RuleHitListOut(
        items=items, total=total, page=page, page_size=page_size
    ).model_dump()


@router.get("/volume")
async def list_volume_baselines(
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(default=None, pattern="^(normal|anomaly)$"),
    q: str = Query(default="", max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """声量基线：各监控分组最近窗口的命中数、均值、标准差与是否异常。"""
    return await volume_service.list_baselines(
        session, status=status or "", q=q, page=page, page_size=page_size
    )


@router.post("/volume/sweep")
async def run_volume_sweep(session: AsyncSession = Depends(get_session)):
    """手动跑一次声量扫描（调试用；线上由调度器每 5 分钟触发）。"""
    return await volume_service.sweep_volume(session)


@router.get("/config")
async def get_rules_config(session: AsyncSession = Depends(get_session)):
    return await settings_service.get_rules_config(session)


@router.put("/config")
async def put_rules_config(
    body: RuleEngineConfigUpdate, session: AsyncSession = Depends(get_session)
):
    """保存阈值配置（脏值会被夹紧到合法区间，不会让告警失真）。"""
    stored = await settings_service.get_setting(session, "rules") or {}
    merged = {**stored, **body.model_dump(exclude_unset=True)}
    config = settings_service.normalize_rules_config(merged)
    await settings_service.set_setting(session, "rules", config)
    return config


# 带路径参数的路由放最后：静态路径（config / volume / hits / preview）必须
# 先注册，否则 "config" 会被 /{rule_id} 的模式吃掉再报 422。
@router.put("/{rule_id}")
async def update_rule(
    rule_id: int, body: RuleEngineUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    rule = await _rule_or_404(session, rule_id)
    payload = body.model_dump(exclude_unset=True)
    if "actions" in payload and payload["actions"] is not None:
        payload["actions"] = [dict(action) for action in payload["actions"]]
    if "keyword_expr" in payload and payload["keyword_expr"] is not None:
        payload["keyword_expr"] = payload["keyword_expr"].strip() or None
    for field, value in payload.items():
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return _rule_view(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: int, session: AsyncSession = Depends(get_session)):
    rule = await _rule_or_404(session, rule_id)
    await session.delete(rule)
    await session.commit()
