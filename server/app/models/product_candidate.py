"""从官方内容抽出的周边候选，以及已经扫过的内容标记。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.communities import DEFAULT_COMMUNITY
from app.models.base import Base


class ProductCandidate(Base):
    __tablename__ = "product_candidates"
    __table_args__ = (
        Index("ix_product_candidates_status", "status", "id"),
        Index("ix_product_candidates_community_name", "community", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    community: Mapped[str] = mapped_column(String(16), default=DEFAULT_COMMUNITY)
    name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    watch_terms: Mapped[list] = mapped_column(JSON, default=list)
    search_keyword: Mapped[str] = mapped_column(String(64))
    price_note: Mapped[str | None] = mapped_column(String(64))
    presale_note: Mapped[str | None] = mapped_column(String(64))
    ship_note: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    source_url: Mapped[str | None] = mapped_column(String(512))
    raw_content_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_product_candidates_raw_content", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / accepted / ignored
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", name="fk_product_candidates_product", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MerchScan(Base):
    """官方内容是否已经做过周边发现。扫过但没有候选也要记，避免每轮重扫。"""

    __tablename__ = "merch_scans"

    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_merch_scans_raw_content", ondelete="CASCADE"),
        primary_key=True,
    )
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # 只在 AI 成功后推进版本。失败保留旧版本，scanned_at 控制重试间隔。
    content_version: Mapped[int] = mapped_column(default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
