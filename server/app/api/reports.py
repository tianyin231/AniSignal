"""通用周期报告 API：公开归档阅读、账号定时订阅管理与管理端按需生成。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, get_optional_user
from app.api.deps import require_admin
from app.communities import COMMUNITIES, ensure_communities_synced
from app.db import get_session
from app.models import PeriodicReport, ReportSubscription, User, UserWebhook
from app.services.periodic_reports import (
    MAX_REPORT_SUBSCRIPTIONS,
    PERIOD_LABELS,
    REPORT_CHANNELS,
    REPORT_PERIODS,
    REPORT_SCOPES,
    deliver_subscription,
    generate_or_get_report,
    scope_display_label,
)

public_router = APIRouter(prefix="/api/reports", tags=["reports"])
account_router = APIRouter(
    prefix="/api/account/reports",
    dependencies=[Depends(get_current_user)],
    tags=["account-reports"],
)
admin_router = APIRouter(
    prefix="/api/admin/reports",
    dependencies=[Depends(require_admin)],
    tags=["admin-reports"],
)


def _report_summary_view(row: PeriodicReport) -> dict:
    return {
        "id": row.id,
        "scope_type": row.scope_type,
        "scope_key": row.scope_key,
        "scope_label": scope_display_label(row.scope_type, row.scope_key),
        "period": row.period,
        "period_key": row.period_key,
        "title": row.title,
        "overview": row.overview,
        "item_count": row.item_count,
        "ai_generated": bool(row.ai_generated),
        "model": row.model,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _report_detail_view(row: PeriodicReport, *, prev_id: int | None = None, next_id: int | None = None) -> dict:
    return {
        **_report_summary_view(row),
        "sections": row.sections or [],
        "markdown": row.markdown or "",
        "window_start": row.window_start,
        "window_end": row.window_end,
        "prev_id": prev_id,
        "next_id": next_id,
    }


def _subscription_view(sub: ReportSubscription, webhooks_by_id: dict[int, UserWebhook]) -> dict:
    wh = webhooks_by_id.get(sub.webhook_id) if sub.webhook_id else None
    return {
        "id": sub.id,
        "name": sub.name,
        "scope_type": sub.scope_type,
        "scope_key": sub.scope_key,
        "scope_label": scope_display_label(sub.scope_type, sub.scope_key),
        "period": sub.period,
        "channel": sub.channel,
        "webhook_id": sub.webhook_id,
        "webhook_name": wh.name if wh else None,
        "webhook_provider": wh.provider if wh else None,
        "enabled": bool(sub.enabled),
        "last_period_key": sub.last_period_key,
        "last_report_id": sub.last_report_id,
        "last_status": sub.last_status,
        "last_error": sub.last_error,
        "last_sent_at": sub.last_sent_at,
        "created_at": sub.created_at,
    }


@public_router.get("")
async def list_reports(
    scope_key: str = Query(default="", max_length=64),
    period: str = Query(default="", pattern="^(|daily|weekly)$"),
    mine: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user: User | None = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """列出已归档的公开分区报告，或登录用户自己的定制监控周期报告。"""
    await ensure_communities_synced(session)
    if mine:
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录后查看个人定制报告")
        conditions = [
            PeriodicReport.scope_type == "custom_bundle",
            PeriodicReport.owner_user_id == user.id,
        ]
    else:
        conditions = [
            PeriodicReport.scope_type == "community",
            PeriodicReport.owner_user_id.is_(None),
        ]
        if scope_key.strip():
            conditions.append(PeriodicReport.scope_key == scope_key.strip())
    if period:
        conditions.append(PeriodicReport.period == period)

    total = await session.scalar(select(func.count(PeriodicReport.id)).where(*conditions)) or 0
    rows = list(
        await session.scalars(
            select(PeriodicReport)
            .where(*conditions)
            .order_by(PeriodicReport.period_key.desc(), PeriodicReport.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    active_communities = [
        {"key": meta.key, "label": meta.label, "domain": meta.domain, "color": meta.color}
        for meta in COMMUNITIES.values()
        if meta.active
    ]
    return {
        "items": [_report_summary_view(r) for r in rows],
        "total": total,
        "communities": active_communities,
    }


@public_router.get("/{report_id}")
async def get_report_detail(
    report_id: int,
    user: User | None = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """读取单期周期报告详情；私有定制报告仅所有者可访问。"""
    await ensure_communities_synced(session)
    report = await session.get(PeriodicReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    if report.owner_user_id is not None and (user is None or user.id != report.owner_user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")

    prev_id = await session.scalar(
        select(PeriodicReport.id)
        .where(
            PeriodicReport.scope_type == report.scope_type,
            PeriodicReport.scope_key == report.scope_key,
            PeriodicReport.period == report.period,
            PeriodicReport.period_key < report.period_key,
        )
        .order_by(PeriodicReport.period_key.desc(), PeriodicReport.id.desc())
        .limit(1)
    )
    next_id = await session.scalar(
        select(PeriodicReport.id)
        .where(
            PeriodicReport.scope_type == report.scope_type,
            PeriodicReport.scope_key == report.scope_key,
            PeriodicReport.period == report.period,
            PeriodicReport.period_key > report.period_key,
        )
        .order_by(PeriodicReport.period_key.asc(), PeriodicReport.id.asc())
        .limit(1)
    )
    return _report_detail_view(report, prev_id=prev_id, next_id=next_id)


# ---------- 账号周期报告订阅 ----------


class SubscriptionCreateIn(BaseModel):
    name: str = Field(default="", max_length=64)
    scope_type: str = Field(default="community", max_length=16)
    scope_key: str = Field(default="", max_length=64)
    period: str = Field(default="weekly", max_length=16)
    channel: str = Field(default="email", max_length=16)
    webhook_id: int | None = None
    enabled: bool = True


class SubscriptionUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    period: str | None = Field(default=None, max_length=16)
    channel: str | None = Field(default=None, max_length=16)
    webhook_id: int | None = None
    enabled: bool | None = None


class SubscriptionSendIn(BaseModel):
    force: bool = False


@account_router.get("/subscriptions")
async def list_subscriptions(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ensure_communities_synced(session)
    subs = list(
        await session.scalars(
            select(ReportSubscription)
            .where(ReportSubscription.user_id == user.id)
            .order_by(ReportSubscription.id.desc())
        )
    )
    webhooks = list(
        await session.scalars(
            select(UserWebhook)
            .where(UserWebhook.user_id == user.id)
            .order_by(UserWebhook.id.desc())
        )
    )
    wh_map = {w.id: w for w in webhooks}
    communities = [
        {"key": meta.key, "label": meta.label, "domain": meta.domain}
        for meta in COMMUNITIES.values()
        if meta.active
    ]
    return {
        "items": [_subscription_view(s, wh_map) for s in subs],
        "max_subscriptions": MAX_REPORT_SUBSCRIPTIONS,
        "schedule_hint": "日报每天 08:30、周报每周一 08:30（北京时间）",
        "communities": communities,
        "webhooks": [
            {"id": w.id, "name": w.name, "provider": w.provider, "enabled": bool(w.enabled)}
            for w in webhooks
        ],
    }


@account_router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: SubscriptionCreateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ensure_communities_synced(session)
    if not user.is_verified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "请先完成邮箱验证后再添加周期报告订阅")

    scope_type = body.scope_type.strip().lower()
    if scope_type not in REPORT_SCOPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "订阅范围仅支持分区或我的定制监控")
    period = body.period.strip().lower()
    if period not in REPORT_PERIODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "周期仅支持 daily 或 weekly")
    channel = body.channel.strip().lower()
    if channel not in REPORT_CHANNELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "推送方式仅支持 email 或 webhook")

    if scope_type == "community":
        scope_key = body.scope_key.strip().lower()
        meta = COMMUNITIES.get(scope_key)
        if meta is None or not meta.active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "所选分区不存在或未启用")
    else:
        scope_key = "all"

    webhook_id: int | None = None
    wh_map: dict[int, UserWebhook] = {}
    if channel == "webhook":
        if not body.webhook_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择一个 Webhook 推送通道")
        wh = await session.get(UserWebhook, body.webhook_id)
        if wh is None or wh.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "所选 Webhook 通道不存在")
        webhook_id = wh.id
        wh_map[wh.id] = wh

    existing_count = (
        await session.scalar(
            select(func.count(ReportSubscription.id)).where(ReportSubscription.user_id == user.id)
        )
        or 0
    )
    if existing_count >= MAX_REPORT_SUBSCRIPTIONS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"每个账号最多创建 {MAX_REPORT_SUBSCRIPTIONS} 项周期报告订阅"
        )

    default_name = f"{scope_display_label(scope_type, scope_key)}{PERIOD_LABELS[period]}"
    name = (body.name.strip() or default_name)[:64]

    sub = ReportSubscription(
        user_id=user.id,
        name=name,
        scope_type=scope_type,
        scope_key=scope_key,
        period=period,
        channel=channel,
        webhook_id=webhook_id,
        enabled=bool(body.enabled),
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return _subscription_view(sub, wh_map)


@account_router.patch("/subscriptions/{sub_id}")
async def update_subscription(
    sub_id: int,
    body: SubscriptionUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(ReportSubscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告订阅不存在")
    if body.name is not None:
        cleaned = body.name.strip()
        if not cleaned:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "订阅名称不能为空")
        sub.name = cleaned[:64]
    if body.period is not None:
        period = body.period.strip().lower()
        if period not in REPORT_PERIODS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "周期仅支持 daily 或 weekly")
        sub.period = period
    if body.channel is not None:
        channel = body.channel.strip().lower()
        if channel not in REPORT_CHANNELS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "推送方式仅支持 email 或 webhook")
        sub.channel = channel
        if channel == "email":
            sub.webhook_id = None
    if sub.channel == "webhook":
        target_wh_id = body.webhook_id if body.webhook_id is not None else sub.webhook_id
        if not target_wh_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择一个 Webhook 推送通道")
        wh = await session.get(UserWebhook, target_wh_id)
        if wh is None or wh.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "所选 Webhook 通道不存在")
        sub.webhook_id = wh.id
    if body.enabled is not None:
        sub.enabled = bool(body.enabled)

    await session.commit()
    await session.refresh(sub)
    wh_map: dict[int, UserWebhook] = {}
    if sub.webhook_id:
        wh = await session.get(UserWebhook, sub.webhook_id)
        if wh:
            wh_map[wh.id] = wh
    return _subscription_view(sub, wh_map)


@account_router.delete("/subscriptions/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    sub_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(ReportSubscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告订阅不存在")
    await session.delete(sub)
    await session.commit()


@account_router.post("/subscriptions/{sub_id}/send")
async def send_subscription_now(
    sub_id: int,
    body: SubscriptionSendIn | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(ReportSubscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告订阅不存在")
    res = await deliver_subscription(
        session,
        sub,
        source="manual",
        force_regenerate=bool(body.force) if body else False,
    )
    if res.get("status") == "throttled":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, res.get("error") or "发送过于频繁")
    return res


# ---------- 管理端按需生成公开分区报告 ----------


class AdminReportGenerateIn(BaseModel):
    community: str = Field(min_length=1, max_length=32)
    period: str = Field(default="weekly", pattern="^(daily|weekly)$")
    force: bool = False


@admin_router.post("/generate")
async def admin_generate_report(
    body: AdminReportGenerateIn,
    session: AsyncSession = Depends(get_session),
):
    await ensure_communities_synced(session)
    key = body.community.strip().lower()
    if key not in COMMUNITIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "分区不存在")
    report = await generate_or_get_report(
        session,
        scope_type="community",
        scope_key=key,
        period=body.period,
        force=body.force,
    )
    if report is None:
        return {"generated": False, "report": None, "message": "本期窗口内暂无可汇总内容"}
    await session.commit()
    await session.refresh(report)
    return {"generated": True, "report": _report_detail_view(report)}
