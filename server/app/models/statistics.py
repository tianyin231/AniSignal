"""面向公开统计页的日级预聚合数据。"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class ContentDailyStat(Base):
    """新入库内容按北京时间日期、平台、身份和关键词聚合。"""

    __tablename__ = "content_daily_stats"
    __table_args__ = (
        Index("ix_content_daily_stats_keyword_date", "keyword", "stat_date"),
        Index("ix_content_daily_stats_date_keyword", "stat_date", "keyword"),
    )

    stat_date: Mapped[date] = mapped_column(Date, primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    identity: Mapped[str] = mapped_column(String(16), primary_key=True)
    # 空字符串表示不区分关键词的基础计数；非空行用于关键词统计。
    keyword: Mapped[str] = mapped_column(String(255), primary_key=True, default="")
    contents_new: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CollectionDailyStat(Base):
    """采集运行按北京时间日期、目标和最终状态聚合。"""

    __tablename__ = "collection_daily_stats"
    __table_args__ = (
        Index("ix_collection_daily_stats_date_status", "stat_date", "status"),
    )

    stat_date: Mapped[date] = mapped_column(Date, primary_key=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey(
            "collect_targets.id",
            name="fk_collection_daily_stats_target_id_collect_targets",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(16), primary_key=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    detailed_runs: Mapped[int] = mapped_column(Integer, default=0)
    items_fetched: Mapped[int] = mapped_column(Integer, default=0)
    items_filtered: Mapped[int] = mapped_column(Integer, default=0)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    parse_failed: Mapped[int] = mapped_column(Integer, default=0)
    official_items: Mapped[int] = mapped_column(Integer, default=0)
    community_items: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
