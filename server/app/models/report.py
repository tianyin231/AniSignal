"""通用周期报告与用户定时订阅模型（日报 / 周报；覆盖分区与用户定制监控合集）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class PeriodicReport(Base):
    """按期归档的通用周期报告：同一分区同刊期只生成一次，跨订阅者复用，可重算。"""

    __tablename__ = "periodic_reports"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_key", "period", "period_key", name="uq_periodic_report"),
        Index("ix_periodic_reports_scope_created", "scope_type", "scope_key", "created_at"),
        Index("ix_periodic_reports_owner", "owner_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # community（公开分区） | custom_bundle（用户定制监控合集，私有）
    scope_type: Mapped[str] = mapped_column(String(16))
    # community key（如 git / wuwa）或 user:<uid>
    scope_key: Mapped[str] = mapped_column(String(64))
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_periodic_reports_owner_user_id_users", ondelete="CASCADE"),
        nullable=True,
    )
    # daily | weekly
    period: Mapped[str] = mapped_column(String(16))
    # 北京时间定界：daily=YYYY-MM-DD，weekly=YYYY-Www
    period_key: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(160))
    overview: Mapped[str] = mapped_column(Text, default="")
    # 结构化分组：[{label, summary, items: [{title, summary, url, detail_url, source, platform, metrics_text, published_at, content_id}]}]
    sections: Mapped[list] = mapped_column(JSON, default=list)
    markdown: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"), default="")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ReportSubscription(Base):
    """用户周期报告推送订阅（支持邮件与已有 Webhook 通道）。"""

    __tablename__ = "report_subscriptions"
    __table_args__ = (
        Index("ix_report_subscriptions_user_enabled", "user_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_report_subscriptions_user_id_users", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64))
    # community | custom_bundle
    scope_type: Mapped[str] = mapped_column(String(16))
    # 分区标识（如 git / wuwa）或 all（定制监控合集）
    scope_key: Mapped[str] = mapped_column(String(64))
    # daily | weekly
    period: Mapped[str] = mapped_column(String(16), default="weekly")
    # email | webhook
    channel: Mapped[str] = mapped_column(String(16), default="email")
    webhook_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_webhooks.id", name="fk_report_subscriptions_webhook_id_user_webhooks", ondelete="SET NULL"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    last_period_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("periodic_reports.id", name="fk_report_subscriptions_last_report_id_periodic_reports", ondelete="SET NULL"),
        nullable=True,
    )
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
