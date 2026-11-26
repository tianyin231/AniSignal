"""自定义网页监控（web_page）端到端测试：通用解析栈、SSRF/304、Admin 目标与 Diff 修订通知、用户定制监控。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.collectors.base import CollectorError
from app.collectors.web_page import (
    FetchedPage,
    WebPageCollector,
    public_web_url,
    web_rule_signature,
)
from app.models import CollectTarget, ContentVersion, CustomFeed, CustomFeedItem, RawContent, User
from app.services.auth import create_session, hash_password
from app.services.collect import run_collection
from app.services.content_diff import build_version_history
from app.services.custom_feeds import sweep_custom_feeds
from app.services.ingest import reparse_contents
from app.services.revision_notifications import load_official_revision_events
from app.services.web_parser import (
    WebParseError,
    extract_list,
    extract_page,
    normalize_web_config,
)

ARTICLE_HTML = """<!doctype html>
<html>
<head>
  <title>AniSignal Release Notes</title>
  <meta property="og:title" content="AniSignal v2.1 发布说明">
  <meta property="og:site_name" content="AniSignal Docs">
  <meta property="og:image" content="https://example.com/assets/banner.png">
  <meta property="article:published_time" content="2026-11-25T08:00:00Z">
  <link rel="alternate" type="application/rss+xml" title="官方更新 RSS" href="/feed.xml">
</head>
<body>
  <header><nav><a href="/">Home</a><a href="/login">Login</a></nav></header>
  <main>
    <article>
      <h1>AniSignal v2.1 发布说明</h1>
      <p>本次更新正式上线轻量自定义网页监控能力，支持单页变更追踪与列表页新增文章发现。</p>
      <div class="live-counter">当前在线人数：9821</div>
      <section id="pricing">
        <h2>方案与价格</h2>
        <table>
          <tr><th>方案</th><th>价格</th></tr>
          <tr><td>基础版</td><td>免费</td></tr>
          <tr><td>专业版</td><td>$19/月</td></tr>
        </table>
      </section>
      <section id="changelog">
        <h2>更新日志</h2>
        <ul>
          <li>新增 CSS/XPath 选择器裁剪与忽略行去噪</li>
          <li>接入 Trafilatura + Readability + html2text 通用解析栈</li>
        </ul>
      </section>
      <img src="/assets/screenshot.png" alt="preview">
    </article>
  </main>
  <footer>Copyright 2026</footer>
</body>
</html>
"""

ARTICLE_HTML_V2 = ARTICLE_HTML.replace("$19/月", "$29/月").replace(
    "<li>接入 Trafilatura + Readability + html2text 通用解析栈</li>",
    "<li>接入 Trafilatura + Readability + html2text 通用解析栈</li>\n          <li>新增列表页自动发现与全文补读</li>",
)

LIST_HTML = """<!doctype html>
<html>
<head>
  <title>项目公告列表</title>
  <meta property="og:site_name" content="OpenProject">
  <link rel="alternate" type="application/atom+xml" title="Atom Feed" href="/atom.xml">
</head>
<body>
  <nav><a href="/about">关于我们</a><a href="/login">登录</a></nav>
  <main>
    <ul class="posts">
      <li>
        <time datetime="2026-11-25">2026-11-25</time>
        <a href="/blog/v2-release?utm_source=twitter#top">v2.0 正式发布：全面支持插件系统</a>
      </li>
      <li>
        <time datetime="2026-11-20">2026-11-20</time>
        <a href="/blog/roadmap-2027">2027 年度路线图与架构规划</a>
      </li>
      <li>
        <a href="/blog/2026/?label=copilot">copilot</a>
      </li>
      <li>
        <a href="/docs/quickstart">快速开始文档</a>
      </li>
      <li>
        <a href="/assets/guide.pdf">下载 PDF 手册</a>
      </li>
      <li>
        <a href="https://external.other/promo">外部推广链接</a>
      </li>
    </ul>
  </main>
</body>
</html>
"""

SPA_HTML = """<!doctype html>
<html><head><title>App</title></head>
<body><div id="root"></div><noscript>Please enable JavaScript to view this app.</noscript></body>
</html>"""


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value, ex=None, px=None, nx: bool = False):
        if key.startswith("anisignal:politeness:"):
            return True
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def pttl(self, key: str):
        return 0

    async def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    async def release_if_match(self, key: str, token: str):
        if self.store.get(key) == token:
            self.store.pop(key, None)


# ---------- 1. 通用网页解析引擎单测 ----------


def test_extract_page_preserves_tables_and_detects_rss():
    page = extract_page(ARTICLE_HTML, "https://example.com/release")
    assert page.title == "AniSignal v2.1 发布说明"
    assert page.site_name == "AniSignal Docs"
    assert page.published_at == datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
    assert "$19/月" in page.text
    assert "新增 CSS/XPath 选择器裁剪与忽略行去噪" in page.text
    assert "https://example.com/assets/banner.png" in page.media
    assert len(page.detected_feeds) == 1
    assert page.detected_feeds[0].url == "https://example.com/feed.xml"
    assert page.detected_feeds[0].feed_type == "rss"
    assert page.spa_warning is False

    # 文档站标题末尾的 ¶ / # 锚点符号自动清理
    docs_page = extract_page("<html><body><h1>Release Notes¶</h1><p>Updated release notes text.</p></body></html>", "https://docs.example.com/rn")
    assert docs_page.title == "Release Notes"


def test_extract_page_css_xpath_exclude_and_ignore_patterns():
    # CSS 选择器 + 排除选择器 + 忽略行规则
    page = extract_page(
        ARTICLE_HTML,
        "https://example.com/release",
        selector="main article",
        exclude_selector=".live-counter, #pricing",
        ignore_patterns=["regex:Trafilatura"],
    )
    assert page.extractor == "selector"
    assert "当前在线人数" not in page.text
    assert "$19/月" not in page.text
    assert "Trafilatura" not in page.text
    assert "新增 CSS/XPath 选择器裁剪与忽略行去噪" in page.text

    # XPath 选择器
    xp_page = extract_page(
        ARTICLE_HTML,
        "https://example.com/release",
        selector="//section[@id='pricing']",
    )
    assert xp_page.extractor == "selector"
    assert "$19/月" in xp_page.text
    assert "更新日志" not in xp_page.text

    # 不匹配的选择器必须显式报错
    with pytest.raises(WebParseError, match="未匹配到选择器"):
        extract_page(ARTICLE_HTML, "https://example.com/release", selector="#nonexistent")


def test_normalize_web_config_rejects_bad_selector_and_regex():
    with pytest.raises(WebParseError):
        normalize_web_config({"selector": "div[[[bad"})
    with pytest.raises(WebParseError):
        normalize_web_config({"link_pattern": "regex:[unclosed"})
    cfg = normalize_web_config({
        "selector": " main ",
        "exclude_selector": ".ads",
        "link_pattern": "/blog/",
        "ignore_patterns": ["在线人数", "regex:\\d+分钟前"],
        "fetch_body": True,
    })
    assert cfg["selector"] == "main"
    assert cfg["ignore_patterns"] == ["在线人数", "regex:\\d+分钟前"]


def test_extract_page_flags_spa_shell():
    page = extract_page(SPA_HTML, "https://spa.example.com/")
    assert page.spa_warning is True


def test_extract_list_cleans_urls_and_filters_by_pattern():
    links, meta = extract_list(LIST_HTML, "https://example.com/news", link_pattern="/blog/")
    assert [link.url for link in links] == [
        "https://example.com/blog/v2-release",
        "https://example.com/blog/roadmap-2027",
    ]
    assert links[0].title == "v2.0 正式发布：全面支持插件系统"
    assert links[0].published_at == datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    assert len(meta["detected_feeds"]) == 1
    assert meta["detected_feeds"][0].url == "https://example.com/atom.xml"


# ---------- 2. SSRF 防护与条件请求 304 ----------


async def test_public_web_url_blocks_private_addresses():
    with pytest.raises(CollectorError, match="私有网络") as exc_info:
        await public_web_url("http://127.0.0.1:8080/secret")
    assert getattr(exc_info.value, "stop_fallback", False) is True
    with pytest.raises(CollectorError, match="私有网络"):
        await public_web_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(CollectorError, match="公开 HTTP"):
        await public_web_url("file:///etc/passwd")


async def test_collector_handles_304_and_invalidates_cache_on_rule_change(monkeypatch):
    calls: list[tuple[str | None, str | None]] = []

    async def fake_fetch(url, *, proxy_url=None, etag=None, last_modified=None, timeout=None):
        calls.append((etag, last_modified))
        if etag == '"v1-etag"':
            return FetchedPage(url=url, status_code=304, body=b"", content_type="", etag=etag, not_modified=True)
        return FetchedPage(
            url=url, status_code=200, body=ARTICLE_HTML.encode(), content_type="text/html; charset=utf-8",
            etag='"v1-etag"', last_modified="Wed, 25 Nov 2026 08:00:00 GMT",
        )

    monkeypatch.setattr("app.collectors.web_page.fetch_webpage_bytes", fake_fetch)

    collector = WebPageCollector()
    collector.source_config = {"name": "发布页", "is_official": True, "web": {"selector": "#pricing"}}
    res1 = await collector.fetch("page_watch", "https://example.com/release", 20)
    assert len(res1.items) == 1
    assert collector.last_http_cache is not None
    assert collector.last_http_cache["etag"] == '"v1-etag"'

    # 同一规则带 ETag → 命中 304 短路
    collector2 = WebPageCollector()
    collector2.source_config = {
        "name": "发布页", "is_official": True,
        "web": {"selector": "#pricing"},
        "_http_cache": collector.last_http_cache,
    }
    res2 = await collector2.fetch("page_watch", "https://example.com/release", 20)
    assert collector2.not_modified is True
    assert res2.items == []
    assert calls[-1][0] == '"v1-etag"'

    # 修改选择器后 rule_sig 变化 → 不带旧 ETag，重新完整解析
    collector3 = WebPageCollector()
    collector3.source_config = {
        "name": "发布页", "is_official": True,
        "web": {"selector": "#changelog"},
        "_http_cache": collector.last_http_cache,
    }
    res3 = await collector3.fetch("page_watch", "https://example.com/release", 20)
    assert collector3.not_modified is False
    assert len(res3.items) == 1
    assert calls[-1][0] is None
    assert web_rule_signature({"selector": "#pricing"}) != web_rule_signature({"selector": "#changelog"})


# ---------- 3. Admin 目标采集、Diff 修订通知、列表补读与预览 ----------


async def test_admin_page_watch_e2e_diff_and_revision_notification(client, session_factory, admin_headers, monkeypatch):
    monkeypatch.setattr("app.collectors.web_page.public_web_url", AsyncMock(side_effect=lambda u: u))
    monkeypatch.setattr("app.services.collect.SessionLocal", session_factory)

    # 非法选择器被 Pydantic 422 拦截
    bad = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "web_page",
            "target_type": "page_watch",
            "target_value": "https://example.com/release",
            "community": "wuwa",
            "web_config": {"selector": "div[[[invalid"},
        },
    )
    assert bad.status_code == 422

    # 创建单页官方监控目标
    created = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "web_page",
            "target_type": "page_watch",
            "target_value": "https://example.com/release",
            "display_name": "官方发布说明",
            "community": "wuwa",
            "is_official": True,
            "web_config": {"exclude_selector": ".live-counter"},
        },
    )
    assert created.status_code == 201
    target_id = created.json()["id"]
    assert created.json()["web_config"] == {"exclude_selector": ".live-counter"}

    html_state = {"body": ARTICLE_HTML, "etag": '"etag-1"'}

    async def fake_fetch(url, *, proxy_url=None, etag=None, last_modified=None, timeout=None):
        if etag == html_state["etag"]:
            return FetchedPage(url=url, status_code=304, body=b"", content_type="", etag=etag, not_modified=True)
        return FetchedPage(
            url=url, status_code=200, body=html_state["body"].encode("utf-8"),
            content_type="text/html; charset=utf-8", etag=html_state["etag"],
        )

    monkeypatch.setattr("app.collectors.web_page.fetch_webpage_bytes", fake_fetch)

    # 首次采集：入库 v1
    fake_redis = _FakeRedis()
    r1 = await run_collection(fake_redis, target_id)
    assert r1["status"] == "success"
    assert r1["items_new"] == 1

    # 第二次采集：ETag 命中 304，不生成新版本
    r2 = await run_collection(fake_redis, target_id)
    assert r2["status"] == "success"
    assert r2["items_found"] == 0

    # 页面实质变更（价格 $19→$29，新增日志条目）
    html_state["body"] = ARTICLE_HTML_V2
    html_state["etag"] = '"etag-2"'
    r3 = await run_collection(fake_redis, target_id)
    assert r3["status"] == "success"

    async with session_factory() as s:
        raw = await s.scalar(select(RawContent).where(RawContent.platform == "web_page"))
        assert raw is not None
        versions = list(
            await s.scalars(
                select(ContentVersion)
                .where(ContentVersion.raw_content_id == raw.id)
                .order_by(ContentVersion.version.asc())
            )
        )
        assert len(versions) == 2
        history = build_version_history(raw.id, versions, platform="web_page")
        assert history["revision_count"] == 1
        assert history["has_source_revision"] is True
        assert history["diff"] is not None

        # 自动接入官方修订通知管线（邮件 / Webhook / Bot / RSS）
        rev_events, _ = await load_official_revision_events(s, communities=["wuwa"])
        assert len(rev_events) == 1
        assert rev_events[0]["content_id"] == raw.id
        assert rev_events[0]["platform"] == "web_page"
        assert any("$29/月" in line or "列表页自动发现" in line for line in rev_events[0]["change_lines"])

        # reparse_contents 可无损重算
        reparse = await reparse_contents(s, "web_page")
        assert reparse["failed"] == 0
        assert reparse["checked"] == 1


async def test_admin_list_watch_and_preview(client, session_factory, admin_headers, monkeypatch):
    monkeypatch.setattr("app.collectors.web_page.public_web_url", AsyncMock(side_effect=lambda u: u))
    monkeypatch.setattr("app.services.collect.SessionLocal", session_factory)

    fetched_urls: list[str] = []

    async def fake_fetch(url, *, proxy_url=None, etag=None, last_modified=None, timeout=None):
        fetched_urls.append(url)
        if url == "https://example.com/news":
            return FetchedPage(url=url, status_code=200, body=LIST_HTML.encode("utf-8"), content_type="text/html")
        body = f"<html><head><title>Detail {url}</title></head><body><main><p>这是文章 {url} 的完整正文内容，包含详细技术说明与版本亮点。</p></main></body></html>"
        return FetchedPage(url=url, status_code=200, body=body.encode("utf-8"), content_type="text/html")

    monkeypatch.setattr("app.collectors.web_page.fetch_webpage_bytes", fake_fetch)

    # 预览端点
    prev = await client.post(
        "/api/admin/web-page/preview",
        headers=admin_headers,
        json={
            "url": "https://example.com/news",
            "mode": "list_watch",
            "web_config": {"link_pattern": "/blog/"},
        },
    )
    assert prev.status_code == 200
    pdata = prev.json()
    assert len(pdata["items"]) == 2
    assert pdata["detected_feeds"][0]["url"] == "https://example.com/atom.xml"

    # 创建 list_watch 目标并采集
    created = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "web_page",
            "target_type": "list_watch",
            "target_value": "https://example.com/news",
            "display_name": "官方博客列表",
            "community": "wuwa",
            "web_config": {"link_pattern": "/blog/", "fetch_body": True},
        },
    )
    assert created.status_code == 201
    target_id = created.json()["id"]

    fetched_urls.clear()
    fake_redis = _FakeRedis()
    res = await run_collection(fake_redis, target_id)
    assert res["status"] == "success"
    assert res["items_new"] == 2
    # 1 次列表页 + 2 次新文章全文补读
    assert len(fetched_urls) == 3

    # 再次采集：已有文章不再重复抓取正文
    fetched_urls.clear()
    res2 = await run_collection(fake_redis, target_id)
    assert res2["status"] == "success"
    assert res2["items_new"] == 0
    assert fetched_urls == ["https://example.com/news"]


# ---------- 4. 用户定制监控（共享单次抓页 + 独立选择器 + Diff 变更与首抓免推） ----------


async def _login_user(session, client, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("Pass1234!"),
        display_name=email.split("@")[0],
        is_verified=True,
        notification_enabled=True,
    )
    session.add(user)
    await session.commit()
    token = await create_session(session, user)
    client.cookies.set("anisignal_session", token)
    return user


async def test_custom_page_watch_shared_fetch_isolated_selectors_and_diff_push(
    client, session, monkeypatch
):
    monkeypatch.setattr("app.collectors.web_page.public_web_url", AsyncMock(side_effect=lambda u: u))
    pushed_feeds: list[tuple[int, int]] = []

    async def fake_push(s, feed, new_count):
        pushed_feeds.append((feed.id, new_count))
        return True

    monkeypatch.setattr("app.services.custom_feeds.push_custom_feed_digest", fake_push)

    # 用户 A 监控 #pricing，用户 B 监控同一 URL 的 #changelog
    user_a = await _login_user(session, client, "alice@example.com")
    resp_a = await client.post(
        "/api/account/custom-feeds",
        json={
            "name": "价格页监控",
            "kind": "page_watch",
            "platform": "web_page",
            "target_value": "https://example.com/release",
            "interval_minutes": 60,
            "push_enabled": True,
            "is_public": True,
            "filters": {"selector": "#pricing"},
        },
    )
    assert resp_a.status_code == 201
    feed_a_id = resp_a.json()["id"]

    # 账号预览端点：非法协议拦截
    bad_scheme = await client.post(
        "/api/account/web-page/preview",
        json={"url": "ftp://example.com/x", "mode": "page_watch"},
    )
    assert bad_scheme.status_code == 400

    user_b = await _login_user(session, client, "bob@example.com")
    resp_b = await client.post(
        "/api/account/custom-feeds",
        json={
            "name": "更新日志监控",
            "kind": "page_watch",
            "platform": "web_page",
            "target_value": "https://example.com/release",
            "interval_minutes": 60,
            "push_enabled": True,
            "is_public": True,
            "filters": {"selector": "#changelog"},
        },
    )
    assert resp_b.status_code == 201
    feed_b_id = resp_b.json()["id"]
    assert user_a.id != user_b.id

    fetch_count = 0
    current_html = {"body": ARTICLE_HTML}

    async def fake_fetch(url, *, proxy_url=None, etag=None, last_modified=None, timeout=None):
        nonlocal fetch_count
        fetch_count += 1
        return FetchedPage(url=url, status_code=200, body=current_html["body"].encode("utf-8"), content_type="text/html")

    monkeypatch.setattr("app.services.custom_feeds.fetch_webpage_bytes", fake_fetch)

    # 第 1 轮：共享源只抓 1 次 HTTP，两人各建初始快照，均不发首次骚扰推送
    fake_redis = _FakeRedis()
    s1 = await sweep_custom_feeds(session, fake_redis)
    assert fetch_count == 1
    assert s1["new_items"] == 2
    assert pushed_feeds == []

    # 复位调度时间模拟下轮轮询
    for feed_id in (feed_a_id, feed_b_id):
        feed = await session.get(CustomFeed, feed_id)
        feed.next_run_at = None
        if feed.source_id:
            from app.models import CustomFeedSource

            src = await session.get(CustomFeedSource, feed.source_id)
            src.next_run_at = None
    await session.commit()

    # 第 2 轮：页面未变 → 0 新条目、0 推送
    s2 = await sweep_custom_feeds(session, fake_redis)
    assert fetch_count == 2
    assert s2["new_items"] == 0
    assert pushed_feeds == []

    # 第 3 轮：仅修改 #pricing 价格（#changelog 保持不变）
    current_html["body"] = ARTICLE_HTML.replace("$19/月", "$29/月")
    for feed_id in (feed_a_id, feed_b_id):
        feed = await session.get(CustomFeed, feed_id)
        feed.next_run_at = None
        if feed.source_id:
            from app.models import CustomFeedSource

            src = await session.get(CustomFeedSource, feed.source_id)
            src.next_run_at = None
    await session.commit()

    s3 = await sweep_custom_feeds(session, fake_redis)
    assert fetch_count == 3
    assert s3["new_items"] == 1
    assert pushed_feeds == [(feed_a_id, 1)]

    # 详情页 API 返回 Diff 变更标签与变更摘录行
    detail_a = await client.get(f"/api/custom/feeds/{feed_a_id}")
    assert detail_a.status_code == 200
    items_a = detail_a.json()["items"]
    assert len(items_a) == 2
    latest_a = items_a[0]
    assert latest_a["title"].startswith("[页面变更")
    assert latest_a["change_label"]
    assert any("$29/月" in line for line in latest_a["change_lines"])

    # 用户 B 的 #changelog 没有变化，仍只有初始快照 1 条
    items_b = list(
        await session.scalars(select(CustomFeedItem).where(CustomFeedItem.feed_id == feed_b_id))
    )
    assert len(items_b) == 1
