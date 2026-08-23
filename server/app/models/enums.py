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
