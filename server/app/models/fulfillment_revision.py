"""已核验商品履约状态的修订，供即时通知追踪变化。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class ProductFulfillmentRevision(Base):
    __tablename__ = "product_fulfillment_revisions"
    __table_args__ = (Index("ix_product_fulfillment_revisions_product_id", "product_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", name="fk_product_fulfillment_revisions_product", ondelete="CASCADE")
    )
    community: Mapped[str] = mapped_column(String(16))
    previous_snapshot: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
