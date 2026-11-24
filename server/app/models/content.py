"""内容三件套：RawContent（原始，不可覆盖）→ ContentVersion（解析产物，可重算）→ MetricSnapshot（指标时间快照）。"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.clock import utcnow
from app.models.base import Base
from app.communities import DEFAULT_COMMUNITY


class RawContent(Base):
    __tablename__ = "raw_contents"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_raw_content"),
        Index("ix_raw_contents_collected_at", "collected_at"),
        Index("ix_raw_contents_published_at", "published_at"),
        Index("ix_raw_contents_comm_hidden_pub", "community", "hidden", "published_at", "id"),
        Index("ix_raw_contents_hidden_pub", "hidden", "published_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(64))
    # 所属游戏分区：首次入库时由采集目标写入，此后不随其他目标的采集翻转
    community: Mapped[str] = mapped_column(String(16), default=DEFAULT_COMMUNITY, index=True)
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
    # 内容审核下架：AI 判定违规/不相关自动置位，管理端可随时切换
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 官方内容实体抽取（日程/兑换码）完成时间；NULL = 待抽取
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 指向最新 ContentVersion.id 的冗余指针（不建反向外键，避免级联删除循环依赖）；
    # 由 ORM after_flush 事件自动同步，消除全表 MAX(version) GROUP BY 衍生表。
    latest_version_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

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
        Index("ix_content_versions_latest_category", "raw_content_id", "version", "category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_content_versions_raw_content_id_raw_contents", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    parsed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    normalized_json: Mapped[dict | None] = mapped_column(JSON)
    parser_version: Mapped[str] = mapped_column(String(16), default="1")
    # 规则分类结果（None = 未分类），随版本重算
    category: Mapped[str | None] = mapped_column(String(32))

    raw_content: Mapped[RawContent] = relationship(back_populates="versions")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snapshots_content_time", "raw_content_id", "captured_at"),
        Index("ix_metric_snapshots_time_content", "captured_at", "raw_content_id"),
        Index("ix_metric_snapshots_content_id", "raw_content_id", "id"),
        Index("ix_metric_snapshots_content_id_view", "raw_content_id", "id", "view_count"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_metric_snapshots_raw_content_id_raw_contents", ondelete="CASCADE"),
    )
    metrics: Mapped[dict] = mapped_column(JSON)
    # 原生整数字段：由 before_insert/before_update 自动从 metrics["view"] 同步，消除 JSON_EXTRACT 全表扫描
    view_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    raw_content: Mapped[RawContent] = relationship(back_populates="snapshots")


class ContentSchedule(Base):
    """从官方公告抽取的活动/维护/卡池日程（AI 层产物，可重算）。"""

    __tablename__ = "content_schedules"
    __table_args__ = (
        UniqueConstraint("raw_content_id", "title", name="uq_content_schedule"),
        Index("ix_content_schedules_community_start", "community", "start_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_content_schedules_raw_content_id_raw_contents", ondelete="CASCADE"),
    )
    community: Mapped[str] = mapped_column(String(32), default=DEFAULT_COMMUNITY)
    title: Mapped[str] = mapped_column(String(200))
    # activity / maintenance / pool / broadcast / delay
    kind: Mapped[str] = mapped_column(String(20))
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime | None] = mapped_column(DateTime)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    delayed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RedeemCode(Base):
    """从官方内容抽取的兑换码（AI 层产物；community+code 唯一）。"""

    __tablename__ = "redeem_codes"
    __table_args__ = (
        UniqueConstraint("community", "code", name="uq_redeem_code"),
        Index("ix_redeem_codes_community_status", "community", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", name="fk_redeem_codes_raw_content_id_raw_contents", ondelete="CASCADE"),
    )
    community: Mapped[str] = mapped_column(String(32), default=DEFAULT_COMMUNITY)
    code: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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
        Index("ix_ai_usage_retry", "task", "ref_id", "status", "created_at"),
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


class ContentMatch(Base):
    """LLM 对两条官方内容是否属于同一事件的可追溯判断。"""

    __tablename__ = "content_matches"
    __table_args__ = (
        UniqueConstraint("left_raw_id", "right_raw_id", name="uq_content_match_pair"),
        Index("ix_content_matches_same_confidence", "is_same_event", "confidence"),
        Index(
            "ix_content_matches_same_conf_pair",
            "is_same_event",
            "confidence",
            "left_raw_id",
            "right_raw_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    left_raw_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", ondelete="CASCADE"), index=True
    )
    right_raw_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", ondelete="CASCADE"), index=True
    )
    is_same_event: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ContentReview(Base):
    """AI 对内容相关性、推广属性和编辑价值的结构化候选判断。"""

    __tablename__ = "content_reviews"
    __table_args__ = (
        Index("ix_content_reviews_eligible_score", "relevant", "promotional", "quality_score"),
    )

    raw_content_id: Mapped[int] = mapped_column(
        ForeignKey("raw_contents.id", ondelete="CASCADE"), primary_key=True
    )
    relevant: Mapped[bool] = mapped_column(Boolean, default=True)
    promotional: Mapped[bool] = mapped_column(Boolean, default=False)
    # 违规判定：色情/政治敏感/欺诈钓鱼/严重谣言等，判定成立时自动隐藏内容
    violation: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_score: Mapped[int] = mapped_column(Integer, default=0)
    category_hint: Mapped[str | None] = mapped_column(String(32))
    duplicate_of_raw_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_contents.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    # AI 提取的正文外链（活动/奖励/H5/新闻页）：[{url, label}]，规则提取作为未审核内容的回退
    links: Mapped[list | None] = mapped_column(JSON, default=None)
    # AI 商品履约预判：{kind, confidence, reason}（kind: self_purchase/pr_sample/forward/none）
    fulfillment: Mapped[dict | None] = mapped_column(JSON, default=None)
    # 资讯摘要及候选类型，绑定内容指纹；编辑筛选不代表事实核实。
    news: Mapped[dict | None] = mapped_column(JSON, default=None)
    model: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def extract_view_count(metrics: dict | None) -> int | None:
    """从指标 JSON 提取播放量整型值；缺失或非数字返回 None。"""
    if not isinstance(metrics, dict):
        return None
    val = metrics.get("view")
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            try:
                return int(float(stripped))
            except ValueError:
                return None
    return None


@event.listens_for(MetricSnapshot, "before_insert")
@event.listens_for(MetricSnapshot, "before_update")
def _sync_snapshot_view_count(_mapper, _connection, target: MetricSnapshot) -> None:
    target.view_count = extract_view_count(target.metrics)


@event.listens_for(Session, "after_flush")
def _sync_raw_content_latest_version_id(session: Session, _flush_context) -> None:
    """ContentVersion 写入或删除后，在同一事务内原子同步 raw_contents.latest_version_id。"""
    new_raw_map = {obj.id: obj for obj in session.new if isinstance(obj, RawContent) and obj.id is not None}
    deleted_raw_ids = {obj.id for obj in session.deleted if isinstance(obj, RawContent) and obj.id is not None}

    added_by_raw: dict[int, tuple[int, int]] = {}
    for obj in session.new:
        if isinstance(obj, ContentVersion) and obj.raw_content_id is not None and obj.id is not None:
            rid = obj.raw_content_id
            if rid in deleted_raw_ids:
                continue
            rank = (int(obj.version or 0), int(obj.id))
            prev = added_by_raw.get(rid)
            if prev is None or rank > prev:
                added_by_raw[rid] = rank

    deleted_version_raw_ids = {
        obj.raw_content_id
        for obj in session.deleted
        if isinstance(obj, ContentVersion)
        and obj.raw_content_id is not None
        and obj.raw_content_id not in deleted_raw_ids
    }

    if not added_by_raw and not deleted_version_raw_ids:
        return

    conn = session.connection()
    raw_table = RawContent.__table__
    cv_table = ContentVersion.__table__

    # 同批新建的 RawContent 直接写入本批最高版本 id，避免多余子查询。
    direct_updates: dict[int, int] = {}
    recompute_raw_ids: set[int] = set(deleted_version_raw_ids)
    for rid, (_ver, vid) in added_by_raw.items():
        if rid in new_raw_map and rid not in deleted_version_raw_ids:
            direct_updates[rid] = vid
        else:
            recompute_raw_ids.add(rid)

    updated_values: dict[int, int | None] = {}
    for rid, vid in direct_updates.items():
        conn.execute(update(raw_table).where(raw_table.c.id == rid).values(latest_version_id=vid))
        updated_values[rid] = vid

    for rid in recompute_raw_ids:
        latest_id = conn.execute(
            select(cv_table.c.id)
            .where(cv_table.c.raw_content_id == rid)
            .order_by(cv_table.c.version.desc(), cv_table.c.id.desc())
            .limit(1)
        ).scalar()
        conn.execute(update(raw_table).where(raw_table.c.id == rid).values(latest_version_id=latest_id))
        updated_values[rid] = latest_id

    for rid, latest_id in updated_values.items():
        obj = new_raw_map.get(rid) or session.identity_map.get((RawContent, (rid,), None))
        if obj is not None:
            set_committed_value(obj, "latest_version_id", latest_id)
