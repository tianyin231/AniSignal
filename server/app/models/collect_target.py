"""采集目标：管理员看板维护的关键词/账号/频率配置，兼作来源健康状态。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class CollectTarget(Base):
    __tablename__ = "collect_targets"
    __table_args__ = (
        UniqueConstraint("platform", "target_type", "target_value", name="uq_target"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    target_type: Mapped[str] = mapped_column(String(32))
    target_value: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 相关性过滤：共现词（标题需至少含一个，空 = 不过滤）与热度门槛（0 = 不过滤）
    co_terms: Mapped[list] = mapped_column(JSON, default=list)
    min_views: Mapped[int] = mapped_column(Integer, default=0)

    # 调度字段（naive UTC）
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)

    # 来源健康："没抓到"必须可见，不能静默（设计文档 §16）
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
