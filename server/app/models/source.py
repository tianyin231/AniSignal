"""账号档案：采集过程中 upsert，跨内容共享。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_source"),
        Index("ix_sources_identity", "identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(255))
    official_title: Mapped[str | None] = mapped_column(String(255))
    follower_count: Mapped[int | None] = mapped_column(BigInteger)
    identity: Mapped[str] = mapped_column(String(16), default="unknown")
    # 信源分级（按粉丝量档位，由 services.source_tier 重算）：
    # high / mid / low / unknown。与 CollectTarget.tier 的采集分级不是一回事。
    tier: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
