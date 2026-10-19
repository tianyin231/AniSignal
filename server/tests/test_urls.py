r"""URL 写入侧收敛（app/services/urls.py）的边界单测。

覆盖 SEC-015 / SEC-035 / SEC-045 的三类判据：站内路径、公网 http(s)、出站目标。
重点是前缀判据挡不住的那几种写法——反斜杠被浏览器规范化成 `/`，于是 `/\evil.com`
与 `https://evil.com\@good.com` 都能把"看起来在站内/另一个 host"的链接变成站外跳转。
"""

import pytest

from app.services.urls import (
    is_internal_path,
    is_public_http_url,
    safe_endpoint,
    safe_link,
)


@pytest.mark.parametrize(
    "value",
    ["/", "/about", "/account?tab=feed#top", "/a/b-c_d.html"],
)
def test_internal_path_accepts_plain_site_paths(value):
    assert is_internal_path(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "//evil.com",  # 协议相对 URL，直接跳出站外
        "/\\evil.com",  # WHATWG 把 \ 规范化成 / → //evil.com
        "/\\/evil.com",
        "/\\\\evil.com",
        "/\tevil.com",  # tab 在解析前被剔除 → //evil.com
        "/\nevil.com",
        "https://evil.com",  # 绝对地址不是站内路径
        "about",  # 相对路径，取决于当前 URL，不收敛
    ],
)
def test_internal_path_rejects_escapes(value):
    assert is_internal_path(value) is False


@pytest.mark.parametrize(
    "value",
    ["https://example.com", "http://example.com/a?b=1#c", "https://sub.example.co.uk/x"],
)
def test_public_http_url_accepts(value):
    assert is_public_http_url(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "about:blank",
        "ftp://example.com/x",
        "//example.com",  # 无 scheme
        "https://",  # 无 host
        "http://127.0.0.1:8080/admin",
        "http://169.254.169.254/latest/meta-data",  # 云元数据
        "http://10.1.2.3/",
        "http://[::1]:9000/",
        "http://localhost:9000/",
        "http://metadata.google.internal/",
        "http://127.0.0.1.nip.io/",  # 解析到内网的公共后缀
        "https://evil.com\\@good.com",  # urlparse 读成 good.com，浏览器去 evil.com
        "https://good.com\\@evil.com",
    ],
)
def test_public_http_url_rejects(value):
    assert is_public_http_url(value) is False


def test_safe_link_falls_back_to_none_for_anything_else():
    assert safe_link(None) is None
    assert safe_link("   ") is None
    assert safe_link("javascript:alert(1)") is None
    assert safe_link(" /about ") == "/about"  # 首尾空白照旧剥掉
    assert safe_link("https://example.com") == "https://example.com"


def test_safe_endpoint_allows_internal_hosts_but_not_other_schemes():
    """自托管 LLM / docker 内网桥接是正当部署形态（SEC-014 的教训），只卡 scheme。"""
    assert safe_endpoint("http://vllm:8000/v1") == "http://vllm:8000/v1"
    assert safe_endpoint("http://astrbot:6185") == "http://astrbot:6185"
    assert safe_endpoint("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
    assert safe_endpoint("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert safe_endpoint("file:///etc/passwd") is None
    assert safe_endpoint("gopher://example.com") is None
    assert safe_endpoint("http://host\\@evil.com") is None
    assert safe_endpoint("") is None
    assert safe_endpoint(None) is None
