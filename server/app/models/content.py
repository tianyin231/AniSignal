"""内容三件套：RawContent（原始，不可覆盖）→ ContentVersion（解析产物，可重算）→ MetricSnapshot（指标时间快照）。"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.models.base import Base


class RawContent(Base):
    __tablename__ = "raw_contents"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_raw_content"),
        Index("ix_raw_contents_collected_at", "collected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", name="fk_raw_contents_source_id_sources")
    )
    url: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    # 原始响应，只增不改（设计铁律：原始数据不可覆盖）
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    # 标准化内容的稳定指纹（不含指标），变化才产生新 ContentVersion
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # 命中关键词溯源（采集目标关键词 + 命中的共现词，多个共同命中一起记录）
    matched_keywords: Mapped[list] = mapped_column(JSON, default=list)

    versions: Mapped[list["ContentVersion"]] = relationship(
        back_populates="raw_content",
        cascade="all, delete-orphan",
        order_by="ContentVersion.version",
    )
    snapshots: Mapped[list["MetricSnapshot"]] = relationship(
        back_populates="raw_content", cascade="all, delete-orphan"
    )


class ContentVersion(Base):
    __tablename__ = "content_versions"
    __table_args__ = (
        UniqueConstraint("raw_content_id", "version", name="uq_content_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_content_versions_raw_content_id_raw_contents", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    parsed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    normalized_json: Mapped[dict | None] = mapped_column(JSON)
    parser_version: Mapped[str] = mapped_column(String(16), default="1")
    # 规则分类结果（None = 未分类），随版本重算
    category: Mapped[str | None] = mapped_column(String(32))

    raw_content: Mapped[RawContent] = relationship(back_populates="versions")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snapshots_content_time", "raw_content_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_metric_snapshots_raw_content_id_raw_contents", ondelete="CASCADE"),
    )
    metrics: Mapped[dict] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    raw_content: Mapped[RawContent] = relationship(back_populates="snapshots")


class ContentTranslation(Base):
    """内容的机翻结果（AI 层产物，可重算；一条内容一个目标语言）。"""

    __tablename__ = "content_translations"

    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_content_translations_raw_content_id_raw_contents", ondelete="CASCADE"),
        primary_key=True,
    )
    lang: Mapped[str] = mapped_column(String(8), default="zh-CN")
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AiUsage(Base):
    """AI 调用记账：token 与费用（管理端 LLM 页统计源）。"""

    __tablename__ = "ai_usage"
    __table_args__ = (
        Index("ix_ai_usage_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
