"""事件与证据链模型（设计文档 §7/§9）。

三条铁律的落点：
- 原始内容 → evidences 只引用不复制；
- 事件结论可修正 → event_revisions 记录每次变更（引擎/人工）；
- 人工锁定后引擎不得覆盖 → events.manual_locked。
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.models.base import Base
from app.communities import DEFAULT_COMMUNITY


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_active_updated", "is_active", "last_updated_at"),
        Index("ix_events_community_active", "community", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    # 所属游戏分区：由聚合引擎按证据的分区写入（与证据保持一致）
    community: Mapped[str] = mapped_column(String(16), default=DEFAULT_COMMUNITY)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_status: Mapped[str | None] = mapped_column(String(16))  # ai/fallback/manual
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    category: Mapped[str] = mapped_column(String(32), index=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", name="fk_events_product_id_products", ondelete="SET NULL"),
        index=True,
    )
    identity: Mapped[str] = mapped_column(String(16), default="official")
    importance: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[str] = mapped_column(String(8), default="medium")  # high/medium/low
    confidence_reason: Mapped[str | None] = mapped_column(Text)
    conclusion: Mapped[str | None] = mapped_column(Text)
    status_label: Mapped[str | None] = mapped_column(String(64))
    # 关键事实（结构化 [{label, value}]）：引擎模板生成，AI 摘要可覆盖
    facts: Mapped[list] = mapped_column(JSON, default=list)
    regions: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    manual_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    evidences: Mapped[list["Evidence"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["EventRevision"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventRevision.id"
    )
    product = relationship("Product", back_populates="events")


class Evidence(Base):
    __tablename__ = "evidences"
    __table_args__ = (
        UniqueConstraint("event_id", "raw_content_id", name="uq_evidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", name="fk_evidences_event_id_events", ondelete="CASCADE"),
        index=True,
    )
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_evidences_raw_content_id_raw_contents", ondelete="CASCADE"),
        index=True,
    )
    # excluded 保留人工排除审计，同时阻止聚合器再次挂回事件。
    role: Mapped[str] = mapped_column(String(16), default="support")  # support/conflict/excluded
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    event: Mapped[Event] = relationship(back_populates="evidences")
    raw_content = relationship("RawContent")


class EventRevision(Base):
    __tablename__ = "event_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", name="fk_event_revisions_event_id_events", ondelete="CASCADE"),
        index=True,
    )
    actor: Mapped[str] = mapped_column(String(16), default="engine")  # engine/admin
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    event: Mapped[Event] = relationship(back_populates="revisions")
