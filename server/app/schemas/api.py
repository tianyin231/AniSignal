"""Admin API 的请求/响应 DTO。"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import Platform, TargetType


class CollectTargetCreate(BaseModel):
    platform: Platform
    target_type: TargetType
    target_value: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    enabled: bool = True


class CollectTargetUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    enabled: bool | None = None
    target_value: str | None = Field(default=None, min_length=1, max_length=255)


class CollectTargetOut(BaseModel):
    id: int
    platform: Platform
    target_type: TargetType
    target_value: str
    display_name: str | None
    interval_minutes: int
    enabled: bool
    next_run_at: datetime | None
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
    metrics: dict | None
    cover: str | None


class PublicContentList(BaseModel):
    items: list[PublicContentItem]
    total: int


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
