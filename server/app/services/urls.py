"""URL 类字段的写入侧收敛（SEC-015 / SEC-035 / SEC-045）。

这些字段的值最终会进前端的 `href`/`src`（公告 link、商品链接、采集详情…），所以保存前
必须收口到「站内路径」或「公网 http(s)」两种形态。

判据用 `URL` 解析而不是字符串前缀：`/\\evil.com` 在 WHATWG 解析下会规范化成
`//evil.com`（站外），而 `javascript:` / `data:` 根本不是 http(s)——这两类都是
"startsWith('/')"式前缀判据挡不住的，与 SEC-040（登录回跳）是同一族问题。

服务端出站目标（AI base_url、bridge endpoint）走 `safe_endpoint`：只卡 scheme，
**不**卡内网主机——自托管 LLM 与 docker 内网桥接地址（`http://astrbot:6185`、
`http://vllm:8000/v1`）是正当部署形态，见 SEC-014 的结论。
"""

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 已知的「解析到本机/内网」主机名与后缀（SEC-021 同一份清单，写入侧先挡一层；
# 域名是否真的解析到内网要等请求时才能知道，这里不做 DNS，避免给设置接口加网络依赖）
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


def is_internal_path(value: str) -> bool:
    """站内路径：单个 `/` 开头，不含反斜杠与控制字符。

    反斜杠必须显式排除——浏览器解析 special scheme 的 URL 时会把路径里的 `\\` 规范化
    成 `/`，于是 `/\\evil.com` 实际是 `https://evil.com/`。控制字符（含 tab/换行）也
    必须在解析前剔除，理由同上。
    """
    if not value or len(value) > _MAX_URL_LEN:
        return False
    if "\\" in value or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
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
        # WHATWG 规范把 special scheme 下的 `\` 当 `/`，实际导航到 evil.com——
        # 服务端判据与浏览器判据在这里分叉，所以含反斜杠的 URL 一律不收。
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
    """管理端「链接」类字段的统一入口：站内路径或公网 http(s)，其余一律 None。

    给会进 `href`/`src` 的字段用（公告 link、商品链接…）。非法值返回 None，由调用方
    决定是 400 还是忽略——不要静默清空，那会让管理员以为存进去了。
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if is_internal_path(raw) or is_public_http_url(raw):
        return raw
    return None


def safe_endpoint(value: object) -> str | None:
    """服务端出站目标：只要求 http(s)，允许内网主机（自托管 LLM / docker 桥接）。"""
    raw = str(value or "").strip()
    if not raw or len(raw) > _MAX_URL_LEN:
        return None
    if "\\" in raw:
        return None  # 同 is_public_http_url：httpx 与浏览器对 `\` 的归一化并不一致
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return raw
