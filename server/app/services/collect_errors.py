"""采集失败原因的分类与对外文案（调度退避、普通用户卡片共用）。"""

import re

# YouTube 频道 RSS（feeds/videos.xml）会间歇性返回 404/5xx，属于上游故障，不代表频道失效。
_YOUTUBE_RSS_TRANSIENT = re.compile(r"error '(?:404|5\d\d)\b[^']*' for url '[^']*/feeds/videos\.xml")
YOUTUBE_RSS_RETRY_MAX_MINUTES = 120
YOUTUBE_RSS_TRANSIENT_HINT = "YouTube RSS 间歇性故障，系统会自动重试"


def is_youtube_rss_transient(platform: str | None, error: str | None) -> bool:
    return platform == "youtube" and bool(error) and bool(_YOUTUBE_RSS_TRANSIENT.search(error or ""))


def user_facing_error(platform: str | None, error: str | None) -> str | None:
    """给普通用户看的一行失败原因：不暴露上游 URL、服务端配置项或异常类型。"""
    if not error:
        return None
    if is_youtube_rss_transient(platform, error):
        return YOUTUBE_RSS_TRANSIENT_HINT
    if "BiliRiskControlError" in error or "-352" in error:
        return "B 站接口暂时限制了访问，系统会自动重试"
    if "CollectorDeferred" in error or "限流" in error or "冷却" in error:
        return "采集暂缓，稍后会自动重试"
    if re.search(r"\b404\b", error):
        return "地址无法访问（404），请检查链接是否正确"
    if re.search(r"Timeout|timed out|ConnectError|ConnectTimeout|ReadTimeout", error):
        return "连接超时，系统会自动重试"
    return "采集失败，系统会自动重试"
