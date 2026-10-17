"""商品档案（批次 3b）：一物一档的档案头、价格/热度曲线、AI 质量反馈聚合、用户状态与到价提醒。

设计借鉴 MyFigureCollection（一物一档）+ 什么值得买（降价提醒）+ Keepa（价格曲线），
全部自写实现，不复制任何闭源代码。

与既有模型的分界：
- `products` 仍是履约进度的事实源，本模块只做「档案扩展」，不改写履约字段；
- 曲线与反馈都挂在 product_id 上，商品被删时级联清理，不回填采集链路；
- 到价提醒的推送完全复用既有通知渠道（QQ 机器人 / OnePush / Webhook / 邮件）。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base

# 发售日历状态（管理端维护，前端按它分组）
RELEASE_STATES = [
    "unknown",  # 未公布
    "announced",  # 已公布发售日
    "presale",  # 预售中
    "on_sale",  # 已发售
    "delayed",  # 延期
    "shipped",  # 已发货/到货
]
RELEASE_STATE_LABELS = {
    "unknown": "未公布",
    "announced": "已公布",
    "presale": "预售中",
    "on_sale": "已发售",
    "delayed": "延期",
    "shipped": "已发货",
}

# 用户对商品的手动标记（已预订 / 已到货 等）
TRACK_STATES = ["watching", "ordered", "arrived", "dropped"]
TRACK_STATE_LABELS = {
    "watching": "关注中",
    "ordered": "已预订",
    "arrived": "已到货",
    "dropped": "已放弃",
}

# 质量反馈情感极性
FEEDBACK_SENTIMENTS = ["negative", "mixed", "positive"]
FEEDBACK_SENTIMENT_LABELS = {"negative": "差评", "mixed": "褒贬不一", "positive": "好评"}

# 简报分节 key（详情页与管理端预览共用）
BRIEF_SECTIONS = ["events", "top_hits", "products", "volume"]


class ProductProfile(Base):
    """商品档案头（products 1:1 扩展）：封面、官方链接、发售日历与当前价格。"""

    __tablename__ = "product_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            name="fk_product_profiles_product_id_products",
            ondelete="CASCADE",
        ),
        unique=True,
    )
    cover_url: Mapped[str | None] = mapped_column(String(512))
    official_url: Mapped[str | None] = mapped_column(String(512))
    official_name: Mapped[str | None] = mapped_column(String(255))
    # 别名/俗称：档案页搜索与价格抽取的补充词
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    release_at: Mapped[datetime | None] = mapped_column(DateTime)
    release_state: Mapped[str] = mapped_column(String(16), default="unknown")
    release_note: Mapped[str | None] = mapped_column(String(255))
    # 当前观测到的最低价（由价格采样写入，管理端也可手动校正）
    price_current: Mapped[float | None] = mapped_column(Float)
    price_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    price_source: Mapped[str | None] = mapped_column(String(64))
    price_observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 最近一次统计出的热度分（当日命中数 + 近 7 日均值的加权）
    heat_score: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ProductPricePoint(Base):
    """价格/热度曲线采样点：一次观测 = 一个时间点上的（价格, 热度, 命中数）。"""

    __tablename__ = "product_price_points"
    __table_args__ = (
        Index("ix_product_price_points_product_time", "product_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            name="fk_product_price_points_product_id_products",
            ondelete="CASCADE",
        ),
        index=True,
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    price: Mapped[float | None] = mapped_column(Float)
    heat: Mapped[float | None] = mapped_column(Float)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    # manual（管理端手工录入）/ auto（正文价格抽取）
    source: Mapped[str] = mapped_column(String(16), default="manual")
    note: Mapped[str | None] = mapped_column(String(255))


class ProductFeedback(Base):
    """AI 质量反馈聚合：一条命中内容一条聚合结论（差评为重点）。"""

    __tablename__ = "product_feedbacks"
    __table_args__ = (
        UniqueConstraint("product_id", "content_id", name="uq_product_feedback_content"),
        Index("ix_product_feedbacks_product_sentiment", "product_id", "sentiment"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            name="fk_product_feedbacks_product_id_products",
            ondelete="CASCADE",
        ),
        index=True,
    )
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "raw_contents.id",
            name="fk_product_feedbacks_content_id_raw_contents",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    sentiment: Mapped[str] = mapped_column(String(16), default="negative")
    # AI 置信度 0~1；管理端可人工覆盖 sentiment/summary
    score: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(String(500))
    source_method: Mapped[str] = mapped_column(String(16), default="ai")  # ai / manual
    model: Mapped[str | None] = mapped_column(String(64))
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ProductTrackState(Base):
    """用户对商品的手动标记：关注中 / 已预订 / 已到货 / 已放弃。"""

    __tablename__ = "product_track_states"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_product_track_state"),
        Index("ix_product_track_states_product_state", "product_id", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_product_track_states_user_id_users",
            ondelete="CASCADE",
        ),
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            name="fk_product_track_states_product_id_products",
            ondelete="CASCADE",
        ),
        index=True,
    )
    state: Mapped[str] = mapped_column(String(16), default="watching")
    note: Mapped[str | None] = mapped_column(String(255))
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ProductPriceAlert(Base):
    """到价提醒：用户设心理价位，采集到低于该价即按既有渠道通知。"""

    __tablename__ = "product_price_alerts"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_product_price_alert"),
        Index("ix_product_price_alerts_product_enabled", "product_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_product_price_alerts_user_id_users",
            ondelete="CASCADE",
        ),
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            name="fk_product_price_alerts_product_id_products",
            ondelete="CASCADE",
        ),
        index=True,
    )
    target_price: Mapped[float] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime)
    triggered_price: Mapped[float | None] = mapped_column(Float)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DailyBrief(Base):
    """每日情报简报（批次 3c）：AI 写成的站内日报，管理端可预览历史与详情。"""

    __tablename__ = "daily_briefs"
    __table_args__ = (
        UniqueConstraint("brief_date", name="uq_daily_brief_date"),
        Index("ix_daily_briefs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    brief_date: Mapped[datetime] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    # 结构化分节：事件汇总 / 重要命中 Top / 商品进展 / 声量回顾
    sections: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="ready")  # ready / sent / failed
    ai_status: Mapped[str] = mapped_column(String(16), default="fallback")  # ai / fallback / skipped
    model: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(300))
    delivered: Mapped[dict] = mapped_column(JSON, default=dict)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
