"""统一解析 schema：所有平台 parser 的输出契约。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.enums import Identity, Platform


class ContentType(StrEnum):
    """与 app.models.enums.ContentType 保持一致（独立声明避免模型层反向依赖 schema）。"""

    VIDEO = "video"
    DYNAMIC = "dynamic"
    POST = "post"
    NOTICE = "notice"
    RSS_ITEM = "rss_item"


class NormalizedAuthor(BaseModel):
    external_id: str | None = None
    name: str | None = None
    official_title: str | None = None
    follower_count: int | None = None
    identity: Identity = Identity.UNKNOWN


class NormalizedContent(BaseModel):
    """单条内容的标准化结果；metrics 不参与内容指纹（见 services/fingerprint.py）。"""

    platform: Platform
    content_type: ContentType
    external_id: str
    author: NormalizedAuthor | None = None
    title: str | None = None
    text: str | None = None
    url: str | None = None
    published_at: datetime | None = None  # aware UTC 或 None
    media: list[str] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)
    extra: dict = Field(default_factory=dict)


class FetchResult(BaseModel):
    """collector 的输出：原始 per-item JSON 列表 + 足够重放采集的元信息。"""

    platform: Platform
    fetch_kind: str  # 例：bilibili_search / bilibili_video / kuro_notices
    items: list[dict]
