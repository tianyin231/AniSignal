"""部署与安全加固回归单测：密码特殊字符、封面逐跳 SSRF、动态反代基址与 Secure Cookie。"""

import socket

import httpx
from sqlalchemy.engine import make_url
from starlette.requests import Request

from app.api.auth import _secure_request
from app.config import Settings, get_settings
from app.services.curation import _cover_data_url
from app.services.ingest import ingest_items
from tests.test_parsers_bilibili import VIDEO_RAW


def test_resolved_database_url_handles_special_password_characters():
    raw_pw = "p@ss:w/rd#1%?&=+"
    s = Settings(database_url="mysql+asyncmy://anisignal@mysql:3306/anisignal", database_password=raw_pw)
    parsed = make_url(s.resolved_database_url)
    assert parsed.username == "anisignal"
    assert parsed.password == raw_pw
    assert parsed.host == "mysql"

    # URL 自带口令时不覆盖
    explicit = Settings(
        database_url="mysql+asyncmy://anisignal:inline@mysql:3306/anisignal",
        database_password="other",
    )
    assert make_url(explicit.resolved_database_url).password == "inline"


async def test_cover_data_url_blocks_redirect_to_private_network(monkeypatch):
    def fake_getaddrinfo(host, *args, **kwargs):
        ip = "93.184.216.34" if host == "cdn.example.com" else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "cdn.example.com":
            return httpx.Response(302, headers={"location": "http://internal.local/latest/meta-data"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG\r\n")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    assert await _cover_data_url("https://cdn.example.com/cover.png") is None
    assert calls == ["https://cdn.example.com/cover.png"]


async def test_seo_and_rss_routes_use_forwarded_or_configured_site_base(client, session, monkeypatch):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    await session.commit()

    headers = {"x-forwarded-proto": "https", "x-forwarded-host": "news.example.org:8443"}
    for path in ("/api/robots.txt", "/api/sitemap.xml", "/api/rss.xml", "/api/ai-news/rss.xml"):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 200
        assert "https://news.example.org:8443" in resp.text
        assert "anisignal.xyz" not in resp.text

    monkeypatch.setenv("ANISIGNAL_PUBLIC_BASE_URL", "https://hub.example.net/")
    get_settings.cache_clear()
    try:
        robots = await client.get("/api/robots.txt", headers=headers)
        assert "Sitemap: https://hub.example.net/sitemap.xml" in robots.text
    finally:
        monkeypatch.delenv("ANISIGNAL_PUBLIC_BASE_URL", raising=False)
        get_settings.cache_clear()


def _make_request(scheme: str, host: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(b"host", host.encode())]
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    return Request(
        {"type": "http", "scheme": scheme, "path": "/", "query_string": b"", "headers": raw_headers, "server": (host.split(":")[0], 80)}
    )


def test_secure_cookie_respects_https_proxy_and_lan_http(monkeypatch):
    monkeypatch.setenv("ANISIGNAL_PUBLIC_BASE_URL", "")
    get_settings.cache_clear()
    try:
        assert _secure_request(_make_request("http", "127.0.0.1:8090")) is False
        assert _secure_request(_make_request("http", "192.168.1.20:8090")) is False
        assert _secure_request(
            _make_request("http", "127.0.0.1:8000", {"x-forwarded-proto": "https", "x-forwarded-host": "sub.example.com"})
        ) is True
    finally:
        monkeypatch.delenv("ANISIGNAL_PUBLIC_BASE_URL", raising=False)
        get_settings.cache_clear()
