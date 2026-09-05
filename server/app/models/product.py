"""商品档案（设计文档 §7：Product -> SKU）。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.models.base import Base
from app.communities import DEFAULT_COMMUNITY

# 履约阶梯固定标签（设计文档 §9.2）；索引即阶段号
FULFILLMENT_STAGES = [
    "暂无可靠到货证据",
    "出现送测样品",
    "首位普通用户到货",
    "多名独立用户到货",
    "相近购买时间样本到货",
    "普遍到货",
]


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    # 所属游戏分区：决定履约证据从哪个分区的内容池匹配
    community: Mapped[str] = mapped_column(String(16), default=DEFAULT_COMMUNITY)
    publisher: Mapped[str | None] = mapped_column(String(255))
    skus: Mapped[list] = mapped_column(JSON, default=list)
    price_note: Mapped[str | None] = mapped_column(String(64))
    presale_at: Mapped[str | None] = mapped_column(String(64))
    balance_window: Mapped[str | None] = mapped_column(String(64))
    original_ship_at: Mapped[str | None] = mapped_column(String(64))
    ship_note: Mapped[str | None] = mapped_column(Text)
    # 命中任一词的内容会关联到该商品（履约类目时）
    watch_terms: Mapped[list] = mapped_column(JSON, default=list)
    # 管理端或 AI 指定的当前阶段（-1 = 跟随引擎计算）
    stage_hint: Mapped[int] = mapped_column(Integer, default=-1)
    stage_hint_source: Mapped[str | None] = mapped_column(String(16))  # manual / ai
    # 阶段判断必须绑定原始内容证据并填写判断理由。
    stage_hint_reason: Mapped[str | None] = mapped_column(Text)
    stage_hint_evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    stage_hint_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 引擎上次计算出的阶段（用于检测推进并写修订）
    computed_stage: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    events = relationship("Event", back_populates="product")
