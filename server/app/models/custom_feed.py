"""用户定制监控：自定义抓取源（UID 动态 / 关键词）与其条目。

定制数据与主内容库（raw_contents，按游戏分区）完全隔离：
- 定制源是"用户个人视角"，可能跨游戏、跨分区，不进公开内容流与事件引擎；
- 公开到全站（is_public）只影响"定制化"频道页是否展示该监控；
- 管理端可用 status=disabled 停用违规监控（enabled 是用户自己的开关，两者独立）。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class CustomFeed(Base):
    __tablename__ = "custom_feeds"
    __table_args__ = (
        Index("ix_custom_feeds_next_run", "next_run_at"),
        Index("ix_custom_feeds_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_custom_feeds_user_id_users", ondelete="CASCADE"),
    )
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # uid / keyword
    platform: Mapped[str] = mapped_column(String(16), default="bilibili")
    # uid 监控存 UID；关键词监控存关键词
    target_value: Mapped[str] = mapped_column(String(128))
    # 关键词监控的播放量门槛（0 = 不限）
    min_views: Mapped[int] = mapped_column(Integer, default=0)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    # 用户自己的启停开关
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 管理端停用（违规等）；disabled 时调度器跳过
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/disabled
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CustomFeedItem(Base):
    __tablename__ = "custom_feed_items"
    __table_args__ = (
        UniqueConstraint("feed_id", "external_id", name="uq_custom_feed_item"),
        Index("ix_custom_feed_items_feed_time", "feed_id", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(
        ForeignKey("custom_feeds.id", name="fk_custom_feed_items_feed_id_custom_feeds", ondelete="CASCADE"),
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(16), default="video")
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(512))
    cover: Mapped[str | None] = mapped_column(String(512))
    author_name: Mapped[str | None] = mapped_column(String(64))
    view: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
