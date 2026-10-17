"""Admin API 的请求/响应 DTO。"""

import re
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


def _none_to_list(v):
    return v if v is not None else []

from app.communities import DEFAULT_COMMUNITY as _DEFAULT_COMMUNITY, community_pattern as _community_pattern
from app.models.enums import Platform, TargetType

COMMUNITY_PATTERN = _community_pattern()


class CollectTargetCreate(BaseModel):
    platform: Platform
    target_type: TargetType
    target_value: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    enabled: bool = True
    co_terms: list[str] = Field(default_factory=list)
    min_views: int = Field(default=0, ge=0, le=10_000_000)
    tier: str = Field(default="general", pattern="^(core|general)$")
    community: str = Field(default=_DEFAULT_COMMUNITY, pattern=COMMUNITY_PATTERN)
    # 监控意图：自然语言描述"这个目标到底在乎什么"（空 = 不送 AI 打标）
    intent: str | None = Field(default=None, max_length=1000)


class CollectTargetUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    enabled: bool | None = None
    target_value: str | None = Field(default=None, min_length=1, max_length=255)
    co_terms: list[str] | None = None
    min_views: int | None = Field(default=None, ge=0, le=10_000_000)
    tier: str | None = Field(default=None, pattern="^(core|general)$")
    community: str | None = Field(default=None, pattern=COMMUNITY_PATTERN)
    intent: str | None = Field(default=None, max_length=1000)


class CollectTargetOut(BaseModel):
    id: int
    platform: Platform
    target_type: TargetType
    target_value: str
    display_name: str | None
    interval_minutes: int
    enabled: bool
    co_terms: list[str] = []
    min_views: int = 0
    tier: str = "general"
    community: str = _DEFAULT_COMMUNITY
    intent: str | None = None
    next_run_at: datetime | None

    @field_validator("co_terms", mode="before")
    @classmethod
    def _co(cls, v):
        return v or []
    last_run_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    last_error: str | None

    model_config = {"from_attributes": True}


class RunTriggerOut(BaseModel):
    job_enqueued: bool
    job_id: str | None = None


class QueueStats(BaseModel):
    pending_jobs: int | None = None


class OverviewOut(BaseModel):
    targets: list[CollectTargetOut]
    queue: QueueStats
    total_contents: int


class ContentItemOut(BaseModel):
    id: int
    platform: Platform
    content_type: str
    external_id: str
    community: str = _DEFAULT_COMMUNITY
    url: str | None
    published_at: datetime | None
    collected_at: datetime
    source_name: str | None = None
    title: str | None = None
    version: int | None = None
    latest_metrics: dict | None = None
    hidden: bool = False
    tier: str = "none"
    matched_keywords: list[str] = []
    review: dict | None = None


class ContentListOut(BaseModel):
    items: list[ContentItemOut]
    total: int


class PublicContentItem(BaseModel):
    id: int
    platform: Platform
    content_type: str
    external_id: str
    url: str | None
    title: str | None
    text_excerpt: str | None
    published_at: datetime | None
    collected_at: datetime
    source_name: str | None
    identity: str
    category: str | None = None
    metrics: dict | None
    cover: str | None
    preview_mode: str  # full / partial / external_only
    matched_keywords: list[str] = []
    community: str = _DEFAULT_COMMUNITY
    delta_view: int | None = None


class PublicContentList(BaseModel):
    items: list[PublicContentItem]
    total: int
    # 账号中心关注流附加：近 24 小时新采集的命中条数（公开接口不返回）
    new_today: int | None = None


class ContentMetricPoint(BaseModel):
    captured_at: datetime
    metrics: dict


class ContentDetailOut(BaseModel):
    id: int
    platform: Platform
    content_type: str
    external_id: str
    url: str | None
    title: str | None
    text: str | None
    text_html: str | None = None  # 含 HTML 的正文经 bleach 消毒后返回
    published_at: datetime | None
    collected_at: datetime
    last_seen_at: datetime | None = None
    source_name: str | None = None
    source_identity: str | None = None
    identity: str
    media: list[str] = []
    metrics: dict | None = None
    metrics_history: list[ContentMetricPoint] = []
    event: dict | None = None
    translation: dict | None = None
    matched_keywords: list[str] = []
    version: int | None = None
    preview_mode: str


class PublicSource(BaseModel):
    name: str
    platform: Platform
    target_type: str
    enabled: bool
    last_success_at: datetime | None
    consecutive_failures: int
    community: str = _DEFAULT_COMMUNITY


class PublicSourceList(BaseModel):
    items: list[PublicSource]


class PublicStats(BaseModel):
    total_contents: int
    by_platform: dict[str, int]
    official_contents: int
    latest_collected_at: datetime | None


class StatisticsTotals(BaseModel):
    total_contents: int
    official_contents: int
    community_contents: int
    period_new: int
    collection_runs: int
    failed_runs: int
    success_rate: float
    items_found: int
    items_new: int
    yield_rate: float
    latest_collection_at: datetime | None


class StatisticsTrendPoint(BaseModel):
    date: date
    total: int
    official: int
    community: int


class StatisticsPlatform(BaseModel):
    platform: str
    total: int
    official: int
    community: int
    share: float


class StatisticsIdentity(BaseModel):
    identity: str
    total: int
    share: float


class StatisticsKeyword(BaseModel):
    keyword: str
    contents: int


class StatisticsCollectionPoint(BaseModel):
    date: date
    runs: int
    failed: int
    items_found: int
    items_new: int
    detailed_runs: int
    items_fetched: int
    items_filtered: int
    items_updated: int


class StatisticsTarget(BaseModel):
    target_id: int
    label: str
    platform: str
    target_type: str
    runs: int
    items_found: int
    items_new: int
    yield_rate: float


class StatisticsRecentRun(BaseModel):
    id: int
    target: str
    platform: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    items_fetched: int | None
    items_filtered: int | None
    items_found: int
    items_new: int
    items_updated: int | None


class StatisticsOverview(BaseModel):
    range_days: int
    generated_at: datetime
    details_since: datetime | None
    totals: StatisticsTotals
    trend: list[StatisticsTrendPoint]
    platforms: list[StatisticsPlatform]
    identities: list[StatisticsIdentity]
    keywords: list[StatisticsKeyword]
    collection: list[StatisticsCollectionPoint]
    targets: list[StatisticsTarget]
    recent_runs: list[StatisticsRecentRun]


class EventSourceOut(BaseModel):
    platform: str
    account: str | None
    title: str | None = None
    region: str
    published_at: datetime | None
    url: str | None
    content_id: int | None = None
    identity: str = "unknown"


class EventCorrectionOut(BaseModel):
    at: datetime
    actor: str
    note: str


class EventOut(BaseModel):
    id: int
    title: str
    summary: str | None
    summary_status: str | None = None
    summary_updated_at: datetime | None = None
    category: str
    community: str = _DEFAULT_COMMUNITY
    product_id: int | None
    product_slug: str | None
    identity: str
    importance: bool
    manual_locked: bool = False
    confidence: str
    confidence_reason: str | None
    conclusion: str | None
    status_label: str | None
    facts: list[dict] = []
    matched_keywords: list[str] = []
    regions: list[str]
    evidence_count: int
    sources: list[EventSourceOut]
    conflicts: list[EventSourceOut] = []
    corrections: list[EventCorrectionOut] = []
    first_seen_at: datetime | None
    latest_evidence_at: datetime | None = None
    updated_at: datetime | None


class EventListOut(BaseModel):
    items: list[EventOut]
    total: int


class ProductStageOut(BaseModel):
    label: str
    state: str  # done/current/pending
    tone: str | None = None
    date: str | None = None
    evidence: int | None = None
    note: str | None = None


class ProductEvidenceOut(BaseModel):
    content_id: int
    title: str | None = None
    platform: str
    account: str | None = None
    identity: str
    category: str | None = None
    published_at: datetime | None = None
    url: str | None = None


class FulfillmentAxisOut(BaseModel):
    key: str
    label: str
    state: str
    state_label: str
    summary: str
    verified_count: int = 0
    candidate_count: int = 0
    updated_at: datetime | None = None
    signal_ids: list[int] = Field(default_factory=list)
    # 无核验数据时来自 AI 履约预判的候选概览（如"2 条自购开箱候选"）
    ai_preview: str = ""


class FulfillmentSnapshotOut(BaseModel):
    official: FulfillmentAxisOut
    consumer: FulfillmentAxisOut
    quality: FulfillmentAxisOut
    sample_count: int = 0
    candidate_total: int = 0
    last_verified_at: datetime | None = None


class ProductSignalOut(BaseModel):
    id: int
    product_id: int
    community: str = _DEFAULT_COMMUNITY
    signal_type: str
    signal_label: str
    actor_type: str
    review_status: str
    occurred_at: datetime | None = None
    summary: str
    confidence: float
    source_method: str
    acquisition_type: str
    claim_quote: str | None = None
    content_id: int
    content_title: str | None = None
    platform: str
    account: str | None = None
    url: str | None = None
    review_note: str | None = None
    updated_at: datetime | None = None
    ai_hint: dict | None = None


class ProductSignalListOut(BaseModel):
    items: list[ProductSignalOut]
    total: int


class ProductSignalUpdate(BaseModel):
    review_status: str
    actor_type: str | None = None
    acquisition_type: str | None = None
    summary: str | None = None
    claim_quote: str | None = None
    note: str = Field(default="", max_length=1000)


class ProductOut(BaseModel):
    id: int
    slug: str
    name: str
    publisher: str | None
    community: str = _DEFAULT_COMMUNITY
    skus: list[str]
    price_note: str | None
    presale_at: str | None
    balance_window: str | None
    original_ship_at: str | None
    ship_note: str | None
    watch_terms: list[str] = []
    stage_hint: int = -1
    current_stage: int
    stage_label: str
    stage_source: str  # signals（旧值仅兼容历史客户端）
    stage_reason: str
    stage_updated_at: datetime | None = None
    stage_evidence_ids: list[int] = []
    official_status: str
    official_evidence_count: int = 0
    consumer_status: str
    consumer_evidence_count: int = 0
    evidences: list[ProductEvidenceOut] = []
    stages: list[ProductStageOut]
    latest_candidate: dict | None = None
    fulfillment: FulfillmentSnapshotOut
    updated_at: datetime | None


class ProductListOut(BaseModel):
    items: list[ProductOut]


class ProductCreate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    publisher: str | None = Field(default=None, max_length=255)
    community: str = Field(default=_DEFAULT_COMMUNITY, pattern=COMMUNITY_PATTERN)
    skus: list[str] = Field(default_factory=list)
    price_note: str | None = Field(default=None, max_length=64)
    presale_at: str | None = Field(default=None, max_length=64)
    balance_window: str | None = Field(default=None, max_length=64)
    original_ship_at: str | None = Field(default=None, max_length=64)
    ship_note: str | None = None
    watch_terms: list[str] = Field(default_factory=list)
    create_bilibili_target: bool = True
    monitor_keyword: str | None = Field(default=None, max_length=255)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    publisher: str | None = Field(default=None, max_length=255)
    community: str | None = Field(default=None, pattern=COMMUNITY_PATTERN)
    skus: list[str] | None = None
    price_note: str | None = Field(default=None, max_length=64)
    presale_at: str | None = Field(default=None, max_length=64)
    balance_window: str | None = Field(default=None, max_length=64)
    original_ship_at: str | None = Field(default=None, max_length=64)
    ship_note: str | None = None
    watch_terms: list[str] | None = None
    stage_hint: int | None = Field(default=None, ge=-1, le=5)
    stage_hint_reason: str | None = None
    stage_hint_evidence_ids: list[int] | None = None


class RuleCreate(BaseModel):
    category: str = Field(min_length=1, max_length=32)
    pattern: str = Field(min_length=1, max_length=64)
    platform: str | None = Field(default=None, max_length=32)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True
    community: str | None = Field(default=None, pattern=COMMUNITY_PATTERN)


class RuleOut(RuleCreate):
    id: int

    model_config = {"from_attributes": True}


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = None
    category: str | None = Field(default=None, max_length=32)
    conclusion: str | None = None
    importance: bool | None = None
    confidence: str | None = Field(default=None, pattern="^(high|medium|low)$")
    status_label: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class EventMergeRequest(BaseModel):
    source_id: int
    target_id: int


class EventSplitRequest(BaseModel):
    content_ids: list[int] = Field(min_length=1)


# ---------- 规则引擎（批次 3a） ----------

RULE_ACTION_PATTERN = "^(tag|promote_event|notify|mark_read)$"
RULE_CHANNEL_PATTERN = "^(webhook|email|qq)$"
RULE_TIER_PATTERN = "^(important|normal|ignore)$"


def _string_list(value, limit: int = 32) -> list[str]:
    """把入参收敛成去重的字符串列表（None → 空列表，标量 → 单元素）。"""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text[:limit])
    return result


class RuleEngineAction(BaseModel):
    """规则动作：tag 需要 value，notify 需要 channels，其余类型不带参数。"""

    type: str = Field(pattern=RULE_ACTION_PATTERN)
    value: str | None = Field(default=None, max_length=32)
    channels: list[str] = Field(default_factory=list)


class RuleEngineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)
    # Mention 风格布尔表达式；空 = 不限关键词
    keyword_expr: str | None = Field(default=None, max_length=1000)
    platforms: list[str] = Field(default_factory=list)
    communities: list[str] = Field(default_factory=list)
    intent_tiers: list[str] = Field(default_factory=list)
    actions: list[RuleEngineAction] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=255)

    @field_validator("platforms", "communities")
    @classmethod
    def _fold_scope(cls, value):
        return _string_list(value)

    @field_validator("intent_tiers")
    @classmethod
    def _check_tiers(cls, value):
        tiers = _string_list(value, 16)
        for tier in tiers:
            if not re.fullmatch(RULE_TIER_PATTERN, tier):
                raise ValueError("intent_tiers 只支持 important / normal / ignore")
        return tiers

    @field_validator("actions")
    @classmethod
    def _check_actions(cls, value):
        for action in value:
            if action.type == "tag" and not (action.value or "").strip():
                raise ValueError("打标签动作必须提供 value")
            if action.type == "notify":
                for channel in action.channels:
                    if not re.fullmatch(RULE_CHANNEL_PATTERN, str(channel).strip()):
                        raise ValueError("notify 渠道只支持 webhook / email / qq")
        return value


class RuleEngineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    keyword_expr: str | None = Field(default=None, max_length=1000)
    platforms: list[str] | None = None
    communities: list[str] | None = None
    intent_tiers: list[str] | None = None
    actions: list[RuleEngineAction] | None = None
    note: str | None = Field(default=None, max_length=255)

    @field_validator("platforms", "communities")
    @classmethod
    def _fold_scope(cls, value):
        return None if value is None else _string_list(value)

    @field_validator("intent_tiers")
    @classmethod
    def _check_tiers(cls, value):
        if value is None:
            return None
        tiers = _string_list(value, 16)
        for tier in tiers:
            if not re.fullmatch(RULE_TIER_PATTERN, tier):
                raise ValueError("intent_tiers 只支持 important / normal / ignore")
        return tiers

    @field_validator("actions")
    @classmethod
    def _check_actions(cls, value):
        if value is None:
            return None
        return RuleEngineCreate._check_actions(value)


class RuleEngineOut(BaseModel):
    id: int
    name: str
    enabled: bool
    priority: int
    keyword_expr: str | None = None
    platforms: list[str] = Field(default_factory=list)
    communities: list[str] = Field(default_factory=list)
    intent_tiers: list[str] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    note: str | None = None
    hit_count: int = 0
    last_hit_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # 解析后的布尔式（管理端展示"这条规则实际在匹配什么"）
    parsed_expr: str | None = None

    model_config = {"from_attributes": True}


class RuleEngineListOut(BaseModel):
    items: list[RuleEngineOut]
    total: int
    page: int
    page_size: int


class RuleHitOut(BaseModel):
    id: int
    rule_id: int
    rule_name: str | None = None
    content_id: int
    content_title: str | None = None
    actions: list[dict] = Field(default_factory=list)
    detail: str | None = None
    error: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class RuleHitListOut(BaseModel):
    items: list[RuleHitOut]
    total: int
    page: int
    page_size: int


class RuleEngineConfigUpdate(BaseModel):
    enabled: bool | None = None
    anomaly_multiplier: float | None = Field(default=None, ge=1.5, le=20.0)
    window_minutes: int | None = Field(default=None, ge=5, le=1440)
    history_windows: int | None = Field(default=None, ge=3, le=168)
    min_samples: int | None = Field(default=None, ge=1, le=200)
    min_hits: int | None = Field(default=None, ge=1, le=10000)
    cooldown_minutes: int | None = Field(default=None, ge=0, le=1440)
    source_tiers: dict | None = None
