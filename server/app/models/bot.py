"""机器人会话订阅与投递记录，独立于个人邮件开关。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class BotTarget(Base):
    __tablename__ = "bot_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    umo: Mapped[str] = mapped_column(String(255), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_scan_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BotDelivery(Base):
    __tablename__ = "bot_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("bot_targets.id", ondelete="CASCADE"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(190), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    media: Mapped[list | None] = mapped_column(JSON, nullable=True)
    rendered_body: Mapped[str | None] = mapped_column(Text)
    receipt: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BotReminder(Base):
    __tablename__ = "bot_reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("bot_targets.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(255))
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    body: Mapped[str] = mapped_column(String(500))
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
