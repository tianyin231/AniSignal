"""旧引擎标识与审计快照；正文和事件仍使用主系统的统一模型。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class NewsArchive(Base):
    __tablename__ = "news_archives"

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    raw_content_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_contents.id", ondelete="SET NULL"), index=True,
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), index=True,
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
