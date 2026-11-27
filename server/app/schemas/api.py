"""Admin API 的请求/响应 DTO。"""

import re
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, field_validator, model_validator

from app.services.urls import safe_link, safe_link_list, safe_media_url


def _none_to_list(v):
    return v if v is not None else []

from app.communities import (
    COLOR_PATTERN,
    COMMUNITY_SLUG_RE,
    CommunitySlug,
    DEFAULT_COMMUNITY as _DEFAULT_COMMUNITY,
    OptionalCommunitySlug,
    community_pattern as _community_pattern,
)
from app.models.enums import Platform, TargetType

COMMUNITY_PATTERN = _community_pattern()

# 出参 URL 收敛：这些字段会进前端的 href/src，采集来的 javascript:/data:/file: 与
# 反斜杠写法一律收敛成 None（前端按「没有链接」渲染），不把脏 URL 下发。
SafeLink = Annotated[str | None, BeforeValidator(safe_link)]
SafeMedia = Annotated[str | None, BeforeValidator(safe_media_url)]
SafeMediaList = Annotated[list[str], BeforeValidator(safe_link_list)]

# 采集目标取值按 (platform, target_type) 收敛：这些值最终会进子进程 argv（bili CLI）
# 或外部请求参数，入口不拦就等于把参数注入面敞开。三条硬规则对所有组合生效：
# 非空、不以 `-` 开头（否则会被 CLI 当选项解析）、不含控制字符。
_TARGET_VALUE_RULES: dict[tuple[str, str], re.Pattern] = {
    ("bilibili", "keyword_search"): re.compile(r"^[^\x00-\x1f\x7f]{1,64}$"),
    ("bilibili", "account_videos"): re.compile(r"^\d{1,20}$"),
    ("bilibili", "account_dynamics"): re.compile(r"^\d{1,20}$"),
    ("miyoushe", "keyword_search"): re.compile(r"^[^\x00-\x1f\x7f]{1,64}$"),
    ("xiaoheihe", "keyword_search"): re.compile(r"^[^\x00-\x1f\x7f]{1,64}$"),
    ("xiaoheihe", "account_dynamics"): re.compile(r"^(?:\d{1,20}|[0-9a-f]{12})$"),
    ("xiaoheihe", "feed"): re.compile(r"^[a-zA-Z0-9_-]{1,32}$"),
    ("steam", "notices"): re.compile(r"^\d{1,10}$"),
    ("kuro_cn", "notices"): re.compile(r"^default$"),
    ("kuro_global", "notices"): re.compile(r"^default$"),
    ("game_official", "notices"): re.compile(r"^(genshin|endfield|nikke)$"),
    # UC 频道 ID，或待创建时解析的 @handle
    ("youtube", "channel_rss"): re.compile(r"^(?:[A-Za-z0-9_-]{2,64}|@[A-Za-z0-9._-]{3,30})$"),
    ("rss", "feed"): re.compile(r"^https?://[^\s]{1,247}$"),
    ("web_page", "page_watch"): re.compile(r"^https?://[^\s]{1,247}$"),
    ("web_page", "list_watch"): re.compile(r"^https?://[^\s]{1,247}$"),
    ("github", "releases"): re.compile(r"^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}$"),
    ("github", "trending"): re.compile(r"^(?:daily|weekly|monthly)(?::[a-z0-9][a-z0-9.+#_-]{0,31})?$"),
}


_YT_CHANNEL_URL_RE = re.compile(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{20,30})")


def normalize_youtube_channel_input(text: str) -> str:
    """频道链接取出 UC ID，@handle 链接取出 @handle（由接口层解析成 UC），其余原样返回。"""
    from app.collectors.youtube_rss import youtube_handle

    m = _YT_CHANNEL_URL_RE.search(text)
    if m:
        return m.group(1)
    return youtube_handle(text) or text


def validate_target_value(platform: object, target_type: object, value: object) -> str:
    """按平台与目标类型收敛 target_value；返回规整值，不合法抛 ValueError。"""
    plat, ttype = str(platform), str(target_type)
    if plat == "youtube" and ttype != "channel_rss":
        raise ValueError("YouTube 目前仅支持频道 RSS")
    if plat == "x" and ttype != "account_dynamics":
        raise ValueError("X 目前仅支持账号动态监控")
    if plat != "x" and (plat, ttype) not in _TARGET_VALUE_RULES:
        raise ValueError(f"{plat} 不支持采集类型 {ttype}")
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError("target_value 不能为空")
    if text.startswith("-"):
        raise ValueError("target_value 不能以 - 开头（会被采集命令当作选项解析）")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ValueError("target_value 不能包含制表符/换行等控制字符")
    if plat == "youtube":
        text = normalize_youtube_channel_input(text)
    if plat == "xiaoheihe" and ttype == "account_dynamics":
        from app.parsers.xiaoheihe import account_target

        text = account_target(text)
    if plat == "steam" and ttype == "notices":
        from app.parsers.steam import extract_app_id

        text = extract_app_id(text)
    if plat == "github":
        if ttype == "trending":
            from app.collectors.github_trending import normalize_trending_target

            text = normalize_trending_target(text)
        else:
            from app.collectors.github import normalize_github_repo

            text = normalize_github_repo(text)
    pattern = _TARGET_VALUE_RULES.get((plat, ttype))
    if pattern is not None and not pattern.fullmatch(text):
        raise ValueError(f"target_value 不符合 {platform}/{target_type} 的格式要求")
    return text


class WebPageConfigIn(BaseModel):
    selector: str = Field(default="", max_length=200)
    exclude_selector: str = Field(default="", max_length=200)
    link_pattern: str = Field(default="", max_length=200)
    ignore_patterns: list[str] = Field(default_factory=list, max_length=10)
    fetch_body: bool | None = None

    @model_validator(mode="after")
    def _validate_web_cfg(self):
        from app.services.web_parser import WebParseError, normalize_web_config

        try:
            cleaned = normalize_web_config(self.model_dump(exclude_none=True))
        except WebParseError as exc:
            raise ValueError(str(exc)) from None
        self.selector = cleaned.get("selector", "")
        self.exclude_selector = cleaned.get("exclude_selector", "")
        self.link_pattern = cleaned.get("link_pattern", "")
        self.ignore_patterns = cleaned.get("ignore_patterns", [])
        self.fetch_body = cleaned.get("fetch_body")
        return self

    def to_storage_dict(self) -> dict:
        from app.services.web_parser import normalize_web_config

        return normalize_web_config(self.model_dump(exclude_none=True))


class WebPagePreviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=255)
    mode: str = Field(default="page_watch", pattern="^(page_watch|list_watch)$")
    web_config: WebPageConfigIn | None = None


class RepoConfigIn(BaseModel):
    include_prerelease: bool | None = None

    def to_storage_dict(self, target_type: str = "releases") -> dict:
        from app.collectors.github import normalize_repo_config

        return normalize_repo_config(self.model_dump(exclude_none=True), target_type=target_type)


class CollectTargetCreate(BaseModel):
    platform: Platform
    target_type: TargetType
    target_value: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    enabled: bool = True
    is_official: bool = False
    co_terms: list[str] = Field(default_factory=list)
    min_views: int = Field(default=0, ge=0, le=10_000_000)
    tier: str = Field(default="general", pattern="^(core|general)$")
    community: CommunitySlug = Field(default=_DEFAULT_COMMUNITY)
    web_config: WebPageConfigIn | None = None
    repo_config: RepoConfigIn | None = None

    @model_validator(mode="after")
    def _check_target_value(self):
        # 格式依赖 platform + target_type 两个字段，只能用 model_validator
        try:
            self.target_value = validate_target_value(
                self.platform, self.target_type, self.target_value
            )
            if self.platform == "github" and self.repo_config is not None:
                self.repo_config.to_storage_dict(str(self.target_type))
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        if self.platform == "game_official" and self.target_value != self.community:
            raise ValueError("游戏官网公告目标必须与所属游戏分区一致")
        return self


class CollectTargetUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    enabled: bool | None = None
    is_official: bool | None = None
    target_value: str | None = Field(default=None, min_length=1, max_length=255)
    co_terms: list[str] | None = None
    min_views: int | None = Field(default=None, ge=0, le=10_000_000)
    tier: str | None = Field(default=None, pattern="^(core|general)$")
    community: OptionalCommunitySlug = Field(default=None)
    web_config: WebPageConfigIn | None = None
    repo_config: RepoConfigIn | None = None


class CommunityCreate(BaseModel):
    key: str = Field(min_length=1, max_length=16, pattern=COMMUNITY_SLUG_RE.pattern)
    label: str = Field(min_length=1, max_length=32)
    en: str = Field(default="", max_length=64)
    color: str = Field(default="blue", pattern=COLOR_PATTERN)
    domain: str = Field(default="game", pattern="^(game|news|feed)$")
    official_bili_mid: str | None = Field(default=None, max_length=24, pattern=r"^(\d{1,20})?$")
    aliases: list[str] = Field(default_factory=list, max_length=16)
    active: bool = True
    ai_summary: bool = False


class CommunityUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=32)
    en: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    domain: str | None = Field(default=None, pattern="^(game|news|feed)$")
    official_bili_mid: str | None = Field(default=None, max_length=24, pattern=r"^(\d{1,20})?$")
    aliases: list[str] | None = Field(default=None, max_length=16)
    active: bool | None = None
    ai_summary: bool | None = None


class CommunityBatchRequest(BaseModel):
    action: str = Field(pattern="^(enable|disable|delete)$")
    keys: list[str] = Field(min_length=1, max_length=100)
    purge_targets: bool = False


class TargetImportPresetsRequest(BaseModel):
    communities: list[str] | None = None


class TargetBatchRequest(BaseModel):
    action: str = Field(pattern="^(enable|disable|delete)$")
    ids: list[int] = Field(default_factory=list, max_length=500)
    community: OptionalCommunitySlug = None


class CollectTargetOut(BaseModel):
    id: int
    platform: str
    target_type: str
    target_value: str
    display_name: str | None
    interval_minutes: int
    enabled: bool
    is_official: bool = False
    web_config: dict = Field(default_factory=dict)
    repo_config: dict = Field(default_factory=dict)
    co_terms: list[str] = []
    min_views: int = 0
    tier: str = "general"
    community: str = _DEFAULT_COMMUNITY
    next_run_at: datetime | None

    @field_validator("co_terms", mode="before")
    @classmethod
    def _co(cls, v):
        return v or []
    last_run_at: datetime | None
    last_success_at: datetime | None
    total_items_new: int | None = None
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


class XiaoheiheShareCreate(BaseModel):
    community: CommunitySlug
    url: str = Field(max_length=512)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
    author: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    like: int | None = Field(default=None, ge=0, le=2_147_483_647)
    reply: int | None = Field(default=None, ge=0, le=2_147_483_647)


class ContentItemOut(BaseModel):
    id: int
    platform: str
    content_type: str
    external_id: str
    community: str = _DEFAULT_COMMUNITY
    url: SafeLink
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


class ContentBriefOut(BaseModel):
    kind: str = "feed"
    headline: str
    tagline: str
    summary: str = ""
    highlights: list[str] = []
    use_cases: list[str] = []
    tags: list[str] = []
    model: str | None = None
    updated_at: datetime | None = None


class PublicContentItem(BaseModel):
    id: int
    platform: str
    content_type: str
    external_id: str
    url: SafeLink
    title: str | None
    text_excerpt: str | None
    published_at: datetime | None
    collected_at: datetime
    source_name: str | None
    identity: str
    category: str | None = None
    metrics: dict | None
    cover: SafeMedia
    media_list: SafeMediaList = []
    media_count: int = 0
    topics: list[str] = []
    brief: ContentBriefOut | None = None
    preview_mode: str  # full / partial / external_only
    matched_keywords: list[str] = []
    community: str = _DEFAULT_COMMUNITY
    delta_view: int | None = None
    revision_count: int = 0
    has_source_revision: bool = False


class ContentFeedStatus(BaseModel):
    automatic: bool
    updated_at: datetime | None
    stale: bool = False


class PublicContentList(BaseModel):
    items: list[PublicContentItem]
    total: int
    # 账号中心关注流附加：近 24 小时新采集的命中条数（公开接口不返回）
    new_today: int | None = None
    feed_status: ContentFeedStatus | None = None


class ContentMetricPoint(BaseModel):
    captured_at: datetime
    metrics: dict


class ContentDetailOut(BaseModel):
    id: int
    community: str = _DEFAULT_COMMUNITY
    news: dict | None = None
    platform: str
    content_type: str
    external_id: str
    url: SafeLink
    title: str | None
    text: str | None
    text_html: str | None = None  # 含 HTML 的正文经 bleach 消毒后返回
    text_truncated: bool = False
    published_at: datetime | None
    collected_at: datetime
    last_seen_at: datetime | None = None
    source_name: str | None = None
    source_identity: str | None = None
    identity: str
    media: SafeMediaList = []
    metrics: dict | None = None
    metrics_history: list[ContentMetricPoint] = []
    event: dict | None = None
    translation: dict | None = None
    brief: ContentBriefOut | None = None
    topics: list[str] = []
    repo_meta: dict | None = None
    matched_keywords: list[str] = []
    version: int | None = None
    revision_count: int = 0
    has_source_revision: bool = False
    preview_mode: str


class ContentDiffSegmentOut(BaseModel):
    op: str  # equal / delete / insert
    text: str


class ContentDiffLineOut(BaseModel):
    op: str  # equal / delete / insert
    old_no: int | None = None
    new_no: int | None = None
    text: str
    segments: list[ContentDiffSegmentOut] | None = None


class ContentDiffHunkOut(BaseModel):
    old_start: int
    new_start: int
    lines: list[ContentDiffLineOut]


class ContentDiffStatsOut(BaseModel):
    added_lines: int = 0
    removed_lines: int = 0
    title_changed: bool = False
    media_added: int = 0
    media_removed: int = 0


class ContentVersionItemOut(BaseModel):
    version: int
    parsed_at: datetime
    origin: str  # source / admin
    title: str
    text_length: int = 0
    media_count: int = 0


class ContentVersionDiffOut(BaseModel):
    from_version: int
    to_version: int
    from_parsed_at: datetime
    to_parsed_at: datetime
    to_origin: str  # source / admin
    title_changed: bool = False
    title_segments: list[ContentDiffSegmentOut] = []
    hunks: list[ContentDiffHunkOut] = []
    media_added: SafeMediaList = []
    media_removed: SafeMediaList = []
    stats: ContentDiffStatsOut = Field(default_factory=ContentDiffStatsOut)
    truncated: bool = False


class ContentVersionHistoryOut(BaseModel):
    content_id: int
    latest_version: int
    revision_count: int = 0
    has_source_revision: bool = False
    versions: list[ContentVersionItemOut] = []
    diff: ContentVersionDiffOut | None = None


class PublicSource(BaseModel):
    name: str
    platform: str
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
    url: SafeLink
    content_id: int | None = None
    identity: str = "unknown"
    revision_count: int = 0
    has_source_revision: bool = False


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
    url: SafeLink = None


class FulfillmentAxisOut(BaseModel):
    key: str
    label: str
    state: str
    state_label: str
    summary: str
    verified_count: int = 0
    candidate_count: int = 0
    automated_count: int = 0
    manual_count: int = 0
    updated_at: datetime | None = None
    signal_ids: list[int] = Field(default_factory=list)
    # 无核验数据时来自 AI 履约预判的候选概览（如"2 条自购开箱候选"）
    ai_preview: str = ""


class FulfillmentSnapshotOut(BaseModel):
    official: FulfillmentAxisOut
    consumer: FulfillmentAxisOut
    quality: FulfillmentAxisOut
    sample_count: int = 0
    observation_count: int = 0
    automated_count: int = 0
    manual_count: int = 0
    automation_mode: str = "automatic"
    candidate_total: int = 0
    last_verified_at: datetime | None = None
    updated_at: datetime | None = None


class ProductSignalOut(BaseModel):
    id: int
    product_id: int
    community: str = _DEFAULT_COMMUNITY
    signal_type: str
    signal_label: str
    actor_type: str
    review_status: str
    review_method: str = "pending"
    review_reason: str = ""
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
    url: SafeLink = None
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


class ProductBriefOut(BaseModel):
    archived: bool = False
    unboxing_accounts: int = 0
    unboxing_from: datetime | None = None
    unboxing_to: datetime | None = None
    unboxing_note: str = ""
    quality_accounts: int = 0
    quality_latest: datetime | None = None
    official_text: str = ""
    official_registered: bool = False


class ProductMonitorOut(BaseModel):
    target_id: int
    keyword: str
    enabled: bool
    interval_minutes: int
    min_views: int
    tier: str


class ProductOut(BaseModel):
    id: int
    slug: str
    name: str
    product_type: str | None = None
    images: SafeMediaList = []
    images_source_url: SafeLink = None
    publisher: str | None
    community: str = _DEFAULT_COMMUNITY
    skus: list[str]
    price_note: str | None
    presale_at: str | None
    balance_window: str | None
    original_ship_at: str | None
    ship_note: str | None
    watch_terms: list[str] = []
    monitor_status: str = "watching"
    source_url: SafeLink = None
    brief: ProductBriefOut = Field(default_factory=ProductBriefOut)
    monitor: ProductMonitorOut | None = None
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


class ProductCandidateOut(BaseModel):
    id: int
    community: str
    name: str
    watch_terms: list[str] = []
    search_keyword: str
    price_note: str | None = None
    presale_note: str | None = None
    ship_note: str | None = None
    source_url: SafeLink = None
    status: str
    created_at: datetime | None = None


class ProductCandidateListOut(BaseModel):
    items: list[ProductCandidateOut]


class ProductCreate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    publisher: str | None = Field(default=None, max_length=255)
    community: CommunitySlug = Field(default=_DEFAULT_COMMUNITY)
    skus: list[str] = Field(default_factory=list)
    price_note: str | None = Field(default=None, max_length=64)
    presale_at: str | None = Field(default=None, max_length=64)
    balance_window: str | None = Field(default=None, max_length=64)
    original_ship_at: str | None = Field(default=None, max_length=64)
    ship_note: str | None = None
    watch_terms: list[str] = Field(default_factory=list)
    create_bilibili_target: bool = True
    monitor_keyword: str | None = Field(default=None, max_length=255)
    monitor_enabled: bool | None = None
    monitor_interval_minutes: int = Field(default=30, ge=5, le=1440)
    monitor_min_views: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    publisher: str | None = Field(default=None, max_length=255)
    community: OptionalCommunitySlug = Field(default=None)
    skus: list[str] | None = None
    price_note: str | None = Field(default=None, max_length=64)
    presale_at: str | None = Field(default=None, max_length=64)
    balance_window: str | None = Field(default=None, max_length=64)
    original_ship_at: str | None = Field(default=None, max_length=64)
    ship_note: str | None = None
    watch_terms: list[str] | None = None
    monitor_enabled: bool | None = None
    monitor_keyword: str | None = Field(default=None, max_length=255)
    monitor_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    monitor_min_views: int | None = Field(default=None, ge=0)
    stage_hint: int | None = Field(default=None, ge=-1, le=5)
    stage_hint_reason: str | None = None
    stage_hint_evidence_ids: list[int] | None = None


class RuleCreate(BaseModel):
    category: str = Field(min_length=1, max_length=32)
    pattern: str = Field(min_length=1, max_length=64)
    platform: str | None = Field(default=None, max_length=32)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True
    community: OptionalCommunitySlug = Field(default=None)


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
