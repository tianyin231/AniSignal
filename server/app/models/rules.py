"""规则引擎与声量基线（批次 3a）。

设计借鉴 Inoreader Rules（if 命中满足条件 then 执行动作）、Brand24 Anomaly
Detector（滑动窗口均值+标准差的热度预警）与 Mention 的布尔关键词语法：
- Rule：一条「条件 → 动作」规则，条件支持 AND/OR/NOT 布尔组合（解析见
  services.boolean_query），动作支持打标签 / 转事件 / 选通知渠道 / 标记已读；
- RuleHit：规则命中台账（一条规则对一条内容只记一次），用于回答"这条内容为什么被推送"；
- VolumeBaseline：按监控任务分组的滑动窗口命中数基线，超过均值 N 倍即热度预警。

以上均为自写实现，不复制任何闭源产品代码。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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

# 规则动作类型
RULE_ACTION_TAG = "tag"  # 打标签（写 RawContent.rule_tags）
RULE_ACTION_EVENT = "promote_event"  # 转为事件（送进事件聚合）
RULE_ACTION_NOTIFY = "notify"  # 走通知渠道推送
RULE_ACTION_READ = "mark_read"  # 标记已读
RULE_ACTIONS = (
    RULE_ACTION_TAG,
    RULE_ACTION_EVENT,
    RULE_ACTION_NOTIFY,
    RULE_ACTION_READ,
)

# 通知动作可选渠道（复用既有渠道：webhook / 邮件 / QQ 机器人）
RULE_CHANNEL_WEBHOOK = "webhook"
RULE_CHANNEL_EMAIL = "email"
RULE_CHANNEL_QQ = "qq"
RULE_CHANNELS = (RULE_CHANNEL_WEBHOOK, RULE_CHANNEL_EMAIL, RULE_CHANNEL_QQ)

# 条件可限定重要性档（复用批次 1 的 intent_tier 三档；空 = 不限）
INTENT_TIERS = ("important", "normal", "ignore")

# 声量基线状态
VOLUME_NORMAL = "normal"
VOLUME_ANOMALY = "anomaly"
VOLUME_STATUSES = (VOLUME_NORMAL, VOLUME_ANOMALY)


class Rule(Base):
    """一条规则引擎规则：条件（关键词布尔式/来源/重要性档）→ 动作列表。"""

    __tablename__ = "rules"
    __table_args__ = (
        Index("ix_rules_enabled_priority", "enabled", "priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 数值越小越先执行（同优先级按 id）
    priority: Mapped[int] = mapped_column(Integer, default=100)
    # 关键词布尔表达式（Mention 风格：AND/OR/NOT、引号短语、括号、容错解析）；空 = 不限关键词
    keyword_expr: Mapped[str | None] = mapped_column(Text, default=None)
    # 来源限定：平台列表（空 = 全部）；社群/分区列表（空 = 全部）
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    communities: Mapped[list] = mapped_column(JSON, default=list)
    # 重要性档限定（空 = 全部，含未打标）
    intent_tiers: Mapped[list] = mapped_column(JSON, default=list)
    # 动作列表：[{"type": "tag", "value": "联名"}, {"type": "notify", "channels": ["webhook"]}, ...]
    actions: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(String(255), default=None)

    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 最近一次执行失败原因（fail-open：单条规则失败只跳过它自己）
    last_error: Mapped[str | None] = mapped_column(String(300), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class RuleHit(Base):
    """规则命中台账：一条规则对一条内容只记一行（重复执行不重复记账）。"""

    __tablename__ = "rule_hits"
    __table_args__ = (
        UniqueConstraint("rule_id", "content_id", name="uq_rule_hit"),
        Index("ix_rule_hits_created", "created_at"),
        Index("ix_rule_hits_content", "content_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules.id", name="fk_rule_hits_rule_id_rules", ondelete="CASCADE"),
        index=True,
    )
    content_id: Mapped[int] = mapped_column(Integer, index=True)
    # 实际执行成功的动作（JSON，与 Rule.actions 同构）
    actions: Mapped[list] = mapped_column(JSON, default=list)
    # 结果摘要：命中依据 / 转出的事件 id / 推送结果 / 已读标记
    detail: Mapped[str | None] = mapped_column(String(500), default=None)
    error: Mapped[str | None] = mapped_column(String(300), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VolumeBaseline(Base):
    """声量基线：一个监控分组在一个时间窗内的命中数与滚动统计。

    分组 = 平台 + 采集时打的关键词溯源（keyword_search / account_* 目标在入库时
    都会写下溯源词，因此分组能对应回监控任务）；滑动窗口逐窗入库，均值/标准差由
    历史窗口滚动计算。
    """

    __tablename__ = "volume_baselines"
    __table_args__ = (
        UniqueConstraint("group_key", "window_started_at", name="uq_volume_window"),
        Index("ix_volume_baselines_status", "status"),
        Index("ix_volume_baselines_group", "group_key", "window_started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 平台 + 溯源关键词，如 "bilibili|鸣潮"
    group_key: Mapped[str] = mapped_column(String(190))
    platform: Mapped[str] = mapped_column(String(32))
    keyword: Mapped[str] = mapped_column(String(255))
    # 能唯一对应上采集目标时回填（仅用于展示）
    target_id: Mapped[int | None] = mapped_column(Integer, default=None)

    window_started_at: Mapped[datetime] = mapped_column(DateTime)
    window_minutes: Mapped[int] = mapped_column(Integer, default=60)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    # 不含本窗的历史滚动统计
    mean: Mapped[float] = mapped_column(Float, default=0.0)
    stddev: Mapped[float] = mapped_column(Float, default=0.0)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    # 判定本窗时使用的倍数阈值
    multiplier: Mapped[float] = mapped_column(Float, default=3.0)
    status: Mapped[str] = mapped_column(String(16), default=VOLUME_NORMAL)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
