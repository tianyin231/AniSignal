"""采集任务运行记录：可观测性的基础数据。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.models.base import Base


class CollectRun(Base):
    __tablename__ = "collect_runs"
    __table_args__ = (
        Index("ix_collect_runs_started_status_target", "started_at", "status", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("collect_targets.id", name="fk_collect_runs_target_id_collect_targets", ondelete="CASCADE"),
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    # 0018 之前的历史记录无法还原这些细分指标，因此允许为空。
    items_fetched: Mapped[int | None] = mapped_column(Integer)
    items_filtered: Mapped[int | None] = mapped_column(Integer)
    items_updated: Mapped[int | None] = mapped_column(Integer)
    # 意图打标判为"忽略"而丢弃的条数：丢弃必须可见，不能静默
    items_ignored: Mapped[int | None] = mapped_column(Integer)
    parse_failed: Mapped[int | None] = mapped_column(Integer)
    official_items: Mapped[int | None] = mapped_column(Integer)
    community_items: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    target = relationship("CollectTarget")
