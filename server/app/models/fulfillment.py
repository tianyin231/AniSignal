"""商品履约信号：候选事实、原始证据与审核修订。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
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


class ProductSignal(Base):
    __tablename__ = "product_signals"
    __table_args__ = (
        Index("ix_product_signals_product_status", "product_id", "review_status"),
        Index("ix_product_signals_product_type", "product_id", "signal_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", name="fk_product_signals_product_id_products", ondelete="CASCADE"),
        index=True,
    )
    signal_type: Mapped[str] = mapped_column(String(32))
    actor_type: Mapped[str] = mapped_column(String(16), default="unknown")
    review_status: Mapped[str] = mapped_column(String(16), default="candidate")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    source_method: Mapped[str] = mapped_column(String(16), default="rule")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    evidences: Mapped[list["ProductSignalEvidence"]] = relationship(
        back_populates="signal", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["ProductSignalRevision"]] = relationship(
        back_populates="signal", cascade="all, delete-orphan"
    )


class ProductSignalEvidence(Base):
    __tablename__ = "product_signal_evidences"
    __table_args__ = (
        UniqueConstraint("signal_id", "raw_content_id", name="uq_product_signal_evidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("product_signals.id", name="fk_signal_evidences_signal_id", ondelete="CASCADE"),
        index=True,
    )
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_signal_evidences_raw_content_id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), default="support")
    claim_quote: Mapped[str | None] = mapped_column(Text)
    acquisition_type: Mapped[str] = mapped_column(String(16), default="unknown")
    independent_source_key: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    signal: Mapped[ProductSignal] = relationship(back_populates="evidences")


class ProductSignalRevision(Base):
    __tablename__ = "product_signal_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("product_signals.id", name="fk_signal_revisions_signal_id", ondelete="CASCADE"),
        index=True,
    )
    actor: Mapped[str] = mapped_column(String(16), default="admin")
    previous_status: Mapped[str | None] = mapped_column(String(16))
    next_status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    signal: Mapped[ProductSignal] = relationship(back_populates="revisions")
