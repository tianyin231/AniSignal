"""资讯编辑的派生产物；原文、版本与已有事件证据仍使用原模型。"""

from datetime import datetime, date

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class EditorialReceipt(Base):
    __tablename__ = "editorial_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    task: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # 收到回复先落库，后续业务事务失败可直接恢复，不再调用模型。
    response: Mapped[str | None] = mapped_column(Text)
    usage_id: Mapped[int | None] = mapped_column(ForeignKey("ai_usage.id", ondelete="SET NULL"))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NewsGrouping(Base):
    __tablename__ = "news_groupings"
    __table_args__ = (Index("ix_news_groupings_story", "story_id", "event_id"),)

    raw_content_id: Mapped[int] = mapped_column(ForeignKey("raw_contents.id", ondelete="CASCADE"), primary_key=True)
    # event 是一次具体发生，story 指向这条事件链的起始事件；后续进展各有自己的 event。
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    story_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    content_version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    review_key: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(24))
    related_event_ids: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(String(500))
    prompt_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NewsEdition(Base):
    __tablename__ = "news_editions"
    __table_args__ = (UniqueConstraint("community", "day", name="uq_news_edition_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    community: Mapped[str] = mapped_column(String(16), default="ai")
    day: Mapped[date] = mapped_column(Date)
    content: Mapped[dict] = mapped_column(JSON)
    rule_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
