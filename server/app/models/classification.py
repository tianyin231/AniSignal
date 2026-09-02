"""分类词典：管理端可编辑的关键词 → 类目规则。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class ClassificationRule(Base):
    __tablename__ = "classification_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    pattern: Mapped[str] = mapped_column(String(64))
    # 空串 = 全平台；否则限定平台
    platform: Mapped[str | None] = mapped_column(String(32), default=None)
    # NULL = 全分区通用（履约类词典等）；否则限定分区（鸣潮/碧蓝航线专属词）
    community: Mapped[str | None] = mapped_column(String(16), default=None)
    # 数值越小越先匹配（同词典内按 priority, id 排序）
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
