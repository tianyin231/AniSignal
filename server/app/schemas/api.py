"""Admin API 的请求/响应 DTO。"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _none_to_list(v):
    return v if v is not None else []

from app.models.enums import Platform, TargetType


class CollectTargetCreate(BaseModel):
    platform: Platform
    target_type: TargetType
    target_value: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    enabled: bool = True
    co_terms: list[str] = Field(default_factory=list)
    min_views: int = Field(default=0, ge=0, le=10_000_000)


class CollectTargetUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    enabled: bool | None = None
    target_value: str | None = Field(default=None, min_length=1, max_length=255)
    co_terms: list[str] | None = None
    min_views: int | None = Field(default=None, ge=0, le=10_000_000)


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
    url: str | None
    published_at: datetime | None
    collected_at: datetime
    source_name: str | None = None
    title: str | None = None
    version: int | None = None
    latest_metrics: dict | None = None


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
    matched_keywords: list[str] = []


class PublicContentList(BaseModel):
    items: list[PublicContentItem]
    total: int


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


class PublicSource(BaseModel):
    name: str
    platform: Platform
    target_type: str
    enabled: bool
    last_success_at: datetime | None
    consecutive_failures: int


class PublicSourceList(BaseModel):
    items: list[PublicSource]


class PublicStats(BaseModel):
    total_contents: int
    by_platform: dict[str, int]
    official_contents: int
    latest_collected_at: datetime | None


class EventSourceOut(BaseModel):
    platform: str
    account: str | None
    region: str
    published_at: datetime | None
    url: str | None
    content_id: int | None = None


class EventCorrectionOut(BaseModel):
    at: datetime
    actor: str
    note: str


class EventOut(BaseModel):
    id: int
    title: str
    summary: str | None
    category: str
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


class ProductOut(BaseModel):
    id: int
    slug: str
    name: str
    publisher: str | None
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
    stages: list[ProductStageOut]
    updated_at: datetime | None


class ProductListOut(BaseModel):
    items: list[ProductOut]


class ProductCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    publisher: str | None = Field(default=None, max_length=255)
    skus: list[str] = Field(default_factory=list)
    price_note: str | None = Field(default=None, max_length=64)
    presale_at: str | None = Field(default=None, max_length=64)
    balance_window: str | None = Field(default=None, max_length=64)
    original_ship_at: str | None = Field(default=None, max_length=64)
    ship_note: str | None = None
    watch_terms: list[str] = Field(default_factory=list)
    stage_hint: int = Field(default=-1, ge=-1, le=5)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    publisher: str | None = Field(default=None, max_length=255)
    skus: list[str] | None = None
    price_note: str | None = Field(default=None, max_length=64)
    presale_at: str | None = Field(default=None, max_length=64)
    balance_window: str | None = Field(default=None, max_length=64)
    original_ship_at: str | None = Field(default=None, max_length=64)
    ship_note: str | None = None
    watch_terms: list[str] | None = None
    stage_hint: int | None = Field(default=None, ge=-1, le=5)


class RuleCreate(BaseModel):
    category: str = Field(min_length=1, max_length=32)
    pattern: str = Field(min_length=1, max_length=64)
    platform: str | None = Field(default=None, max_length=32)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


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
