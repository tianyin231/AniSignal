"""GitHub Trending 分期刊物归档（日刊 / 周刊 / 月刊）：按北京时间期次定格榜单快照以支持跨期去重与上榜打标。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class GitTrendingEdition(Base):
    __tablename__ = "git_trending_editions"
    __table_args__ = (
        UniqueConstraint("community", "period", "period_key", "language", name="uq_git_trending_edition"),
        Index("ix_git_trending_community_period", "community", "period", "period_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    community: Mapped[str] = mapped_column(String(16))
    period: Mapped[str] = mapped_column(String(16))  # daily / weekly / monthly
    period_key: Mapped[str] = mapped_column(String(32))  # 2026-11-26 / 2026-W48 / 2026-11
    language: Mapped[str] = mapped_column(String(32), default="")
    entries: Mapped[list] = mapped_column(JSON, default=list)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
