"""输出层模型：RSS feed 只读令牌与通用 Webhook 通知渠道（含投递记录）。

设计借鉴 Inoreader「把过滤结果再输出成 RSS」与 Google Alerts：
- 每个监控任务、每个事件各有一份只读订阅源，URL 带 token（secrets.token_urlsafe）；
- token 按 feed 独立管理：撤销即失效，重建即换新址（旧址同时失效）；
- Webhook 是用户级通知渠道，命中时 POST JSON（字段兼容 n8n），失败重试并留投递记录。

RSSHub（AGPL）只提供思路，代码全部自写。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base

# feed 种类：target = 管理端采集目标（监控任务）；event = 归并事件
FEED_KIND_TARGET = "target"
FEED_KIND_EVENT = "event"
FEED_KINDS = (FEED_KIND_TARGET, FEED_KIND_EVENT)

# Webhook 推送范围
WEBHOOK_TIER_ALL = "all"
WEBHOOK_TIER_IMPORTANT = "important"
WEBHOOK_TIERS = (WEBHOOK_TIER_IMPORTANT, WEBHOOK_TIER_ALL)


class FeedToken(Base):
    """一个监控任务/事件对应一条只读令牌（kind + ref_id 唯一）。

    status=revoked 时链接立即失效；重建会换新 token 并把 generation +1，
    便于回答"这个订阅地址为什么突然不好用了"。
    """

    __tablename__ = "feed_tokens"
    __table_args__ = (
        UniqueConstraint("kind", "ref_id", name="uq_feed_token_ref"),
        UniqueConstraint("token", name="uq_feed_token_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # target / event
    ref_id: Mapped[int] = mapped_column(Integer)
    token: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / revoked
    generation: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)


class WebhookConfig(Base):
    """用户配置的通用 Webhook 通知渠道（一个账号可配多个）。"""

    __tablename__ = "webhook_configs"
    __table_args__ = (
        Index("ix_webhook_configs_user", "user_id"),
        Index("ix_webhook_configs_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id", name="fk_webhook_configs_user_id_users", ondelete="CASCADE"
        )
    )
    name: Mapped[str] = mapped_column(String(64), default="Webhook")
    url: Mapped[str] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 推送范围：all = 推送全部命中（默认，Google Alerts 式）；important = 只推重要命中
    tier_filter: Mapped[str] = mapped_column(String(16), default=WEBHOOK_TIER_ALL)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class WebhookDelivery(Base):
    """Webhook 投递记录：一次 POST 一行，重试次数与失败原因可见。

    dedupe_key 按 webhook + 内容去重（同一内容不重复打扰），测试投递留空。
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_webhook_delivery_dedupe"),
        Index("ix_webhook_deliveries_hook_created", "webhook_id", "created_at"),
        Index("ix_webhook_deliveries_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    webhook_id: Mapped[int] = mapped_column(
        ForeignKey(
            "webhook_configs.id",
            name="fk_webhook_deliveries_webhook_id_webhook_configs",
            ondelete="CASCADE",
        ),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    # hit = 关键词命中；test = 手动测试
    kind: Mapped[str] = mapped_column(String(16), default="hit")
    content_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict | None] = mapped_column(JSON)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/sent/failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
