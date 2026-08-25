"""业务枚举：存库用字符串列（避免原生 DB enum，迁移更稳），Pydantic 层负责校验。"""

from enum import StrEnum


class Platform(StrEnum):
    BILIBILI = "bilibili"
    MIYOUSHE = "miyoushe"
    KURO_CN = "kuro_cn"
    KURO_GLOBAL = "kuro_global"
    YOUTUBE = "youtube"


class TargetType(StrEnum):
    KEYWORD_SEARCH = "keyword_search"
    ACCOUNT_VIDEOS = "account_videos"
    ACCOUNT_DYNAMICS = "account_dynamics"
    NOTICES = "notices"
    CHANNEL_RSS = "channel_rss"


class ContentType(StrEnum):
    VIDEO = "video"
    DYNAMIC = "dynamic"
    POST = "post"
    NOTICE = "notice"
    RSS_ITEM = "rss_item"


class Identity(StrEnum):
    """来源身份，与 web 端 Identity 标签体系一致（官方/送测/用户/媒体/商家/未知）。"""

    OFFICIAL = "official"
    SEEDED = "seeded"
    USER = "user"
    MEDIA = "media"
    MERCHANT = "merchant"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Category(StrEnum):
    """内容/事件类目（履约词典，设计文档 §8.1）；official_announce 为官方公告兜底类。"""

    OFFICIAL_ANNOUNCE = "official_announce"
    VERSION_UPDATE = "version_update"
    PRESALE = "presale"
    BALANCE = "balance"
    DELAY = "delay"
    SHIPPING = "shipping"
    ARRIVED = "arrived"
    UNBOXING = "unboxing"
    REVIEW = "review"
    QUALITY = "quality"
    PROMOTION = "promotion"
    OTHER = "other"


# 官方公告类（可跨平台合并的类目）
ANNOUNCE_CATEGORIES = {
    Category.OFFICIAL_ANNOUNCE,
    Category.VERSION_UPDATE,
    Category.PRESALE,
    Category.BALANCE,
    Category.DELAY,
}

# 商品履约类（挂到商品事件的类目）
FULFILLMENT_CATEGORIES = {
    Category.PRESALE,
    Category.BALANCE,
    Category.DELAY,
    Category.SHIPPING,
    Category.ARRIVED,
    Category.UNBOXING,
    Category.REVIEW,
    Category.QUALITY,
}
