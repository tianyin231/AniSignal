"""URL 类字段的收敛工具（写入侧与出参侧共用一套判据）。

这些值最终会进前端的 `href`/`src`（公告 link、内容链接、封面图…），所以只允许两种
形态：站内路径，或公网 http(s)。判据必须用 `URL` 解析而不是字符串前缀——
`/\\evil.com` 在 WHATWG 解析下会被规范化成 `//evil.com`（站外），`javascript:` /
`data:` / `file:` 更不是 http(s)，这些都挡不住 startsWith('/') 式判据。
"""

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 已知的「解析到本机/内网」主机名与后缀：写入侧先挡一层，不做 DNS（避免给设置接口
# 加网络依赖）；域名是否真的解析到内网只能在请求时判，那是出站侧的事。
_INTERNAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"})
_INTERNAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".nip.io",
    ".sslip.io",
    ".localtest.me",
    ".vcap.me",
)

_MAX_URL_LEN = 512


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def is_internal_path(value: str) -> bool:
    """站内路径：单个 `/` 开头，不含反斜杠与控制字符。

    反斜杠必须显式排除——浏览器解析 special scheme 时会把路径里的 `\\` 规范化成
    `/`，于是 `/\\evil.com` 实际是 `https://evil.com/`。
    """
    if not value or len(value) > _MAX_URL_LEN:
        return False
    if "\\" in value or _has_control_chars(value):
        return False
    return value.startswith("/") and not value.startswith("//")


def _is_internal_ip_literal(host: str) -> bool:
    """host 是 IP 字面量且落在不可公开访问区间（回环/私网/链路本地/保留/组播/未指定）。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)  # ::ffff:127.0.0.1 的 is_loopback 是 False
    if mapped is not None:
        ip = mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_public_http_url(value: str) -> bool:
    """公网 http(s) URL：scheme 白名单 + host 不是内网 IP 字面量或已知内网主机名。"""
    if not value or len(value) > _MAX_URL_LEN:
        return False
    if "\\" in value:
        # `urlparse` 读 `https://evil.com\@good.com` 的 host 是 good.com，而浏览器按
        # WHATWG 规范把 special scheme 下的 `\` 当 `/`，实际导航到 evil.com——服务端
        # 判据与浏览器判据在这里分叉，所以含反斜杠的 URL 一律不收。
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _INTERNAL_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        return False
    return not _is_internal_ip_literal(host)


def safe_link(value: object) -> str | None:
    """会进 `href` 的字段统一走这里：站内路径或公网 http(s)，其余一律 None。

    返回 None 表示「这个值不能下发给前端」，由调用方决定是 400 还是丢弃——不要
    静默清空后当成保存成功，那会让管理员以为存进去了。
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if is_internal_path(raw) or is_public_http_url(raw):
        return raw
    return None


def safe_media_url(value: object) -> str | None:
    """图片/视频地址（进 `src`）：只要求 http(s)，允许内网主机。

    媒体是浏览器侧加载、不是服务端出站，所以不套公网主机限制（自托管图床合法）；
    但 `javascript:` / `data:` / `file:` 一律拒——它们不是 http(s)。
    """
    raw = str(value or "").strip()
    if not raw or len(raw) > _MAX_URL_LEN or "\\" in raw:
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return raw


def safe_endpoint(value: object) -> str | None:
    """服务端出站目标（AI base_url 等）：只卡 scheme，**不**卡内网主机。

    自托管 LLM 与 docker 内网桥接地址（`http://astrbot:6185`、`http://vllm:8000/v1`）
    是正当部署形态，拒内网会把这类部署打死。
    """
    raw = str(value or "").strip()
    if not raw or len(raw) > _MAX_URL_LEN:
        return None
    if "\\" in raw:
        return None  # httpx 与浏览器对 `\` 的归一化不一致，一律不收
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return raw


def safe_link_list(values: object) -> list[str]:
    """批量收敛（媒体列表等）：逐个判定，丢掉不合法的，不整体失败。"""
    if not isinstance(values, (list, tuple)):
        return []
    return [item for item in (safe_media_url(v) for v in values) if item]
