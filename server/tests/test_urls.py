"""URL 收敛判据：站内路径 / 公网 http(s) / 出站端点 / 媒体地址。"""

import pytest

from app.services.urls import (
    is_internal_path,
    is_public_http_url,
    safe_endpoint,
    safe_link,
    safe_link_list,
    safe_media_url,
)


@pytest.mark.parametrize(
    "value",
    [
        "/account",
        "/contents/1?tab=all#x",
    ],
)
def test_internal_path_accepts(value):
    assert is_internal_path(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "//evil.com",
        "/\\evil.com",  # 浏览器把 \ 归一成 /，实际是 //evil.com
        "/\t/evil.com",
        "https://good.com",
        "/x\ny",
    ],
)
def test_internal_path_rejects(value):
    assert is_internal_path(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "https://good.com/a?b=1",
        "http://good.com",
    ],
)
def test_public_http_url_accepts(value):
    assert is_public_http_url(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "https://evil.com\\@good.com",  # 反斜杠：服务端与浏览器判据分叉
        "https:evil.com",  # 无 // → hostname 为空
        "http:/evil.com",
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest/meta-data/",
        "https://foo.nip.io/x",
        "https://[::1]/x",
        "",
    ],
)
def test_public_http_url_rejects(value):
    assert is_public_http_url(value) is False


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/account", "/account"),
        ("https://good.com/a", "https://good.com/a"),
        ("javascript:alert(1)", None),
        ("//evil.com", None),
        ("/\\evil.com", None),
        ("  https://good.com/a  ", "https://good.com/a"),
        (None, None),
        ("", None),
    ],
)
def test_safe_link(value, expected):
    assert safe_link(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://cdn.example.com/a.png", "https://cdn.example.com/a.png"),
        ("http://127.0.0.1:8080/a.png", "http://127.0.0.1:8080/a.png"),  # 浏览器侧加载，允许内网
        ("javascript:alert(1)", None),
        ("data:image/svg+xml;base64,AAA", None),
        ("file:///etc/passwd", None),
        ("/relative.png", None),
    ],
)
def test_safe_media_url(value, expected):
    assert safe_media_url(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("http://astrbot:6185", "http://astrbot:6185"),
        ("http://vllm:8000/v1", "http://vllm:8000/v1"),
        ("https://api.example.com/v1", "https://api.example.com/v1"),
        ("javascript:alert(1)", None),
        ("file:///etc/passwd", None),
        ("https://evil.com\\@good.com", None),
        ("", None),
    ],
)
def test_safe_endpoint_allows_internal_host(value, expected):
    """出站目标允许自托管内网主机，但 scheme 必须 http(s)。"""
    assert safe_endpoint(value) == expected


def test_safe_link_list_drops_invalid():
    assert safe_link_list(
        ["https://cdn/a.png", "javascript:alert(1)", None, "http://x/b.png"]
    ) == ["https://cdn/a.png", "http://x/b.png"]
    assert safe_link_list("not-a-list") == []
