"""RSS 输出与 Webhook 渠道单测：token 鉴权/撤销/重建、feed 条目、管理端 API、命中推送与重试。"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.config import get_settings
from app.models import (
    CollectTarget,
    Event,
    Evidence,
    FeedToken,
    RawContent,
    Source,
    User,
    UserKeyword,
    WebhookConfig,
    WebhookDelivery,
)
from app.services import feeds as feeds_module
from app.services import webhooks as webhooks_module
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import VIDEO_RAW


def _video(bvid: str, title: str) -> dict:
    item = dict(VIDEO_RAW)
    item["bvid"] = bvid
    item["title"] = title
    item["url"] = f"https://www.bilibili.com/video/{bvid}"
    return item


async def _source(session, platform: str, name: str) -> Source:
    source = await session.scalar(
        select(Source).where(Source.platform == platform, Source.external_id == name)
    )
    if source is None:
        source = Source(platform=platform, external_id=name, name=name, identity="official")
        session.add(source)
        await session.flush()
    return source


async def _content(
    session,
    *,
    external_id: str,
    title: str,
    keywords: list[str],
    published_at=None,
    intent_tier: str | None = None,
    hidden: bool = False,
    source_name: str | None = "B站源",
) -> RawContent:
    stats = await ingest_items(session, "bilibili_search", [_video(external_id, title)])
    assert stats.items_new == 1, stats.errors
    raw = await session.scalar(
        select(RawContent).where(RawContent.external_id == external_id)
    )
    raw.matched_keywords = keywords
    if published_at is not None:
        raw.published_at = published_at
    if intent_tier is not None:
        raw.intent_tier = intent_tier
    raw.hidden = hidden
    if source_name is not None:
        raw.source_id = (await _source(session, raw.platform, source_name)).id
    await session.commit()
    return raw


async def _target(session, *, value: str = "鸣潮", display_name: str | None = None) -> CollectTarget:
    target = CollectTarget(
        platform="bilibili",
        target_type="keyword_search",
        target_value=value,
        display_name=display_name,
    )
    session.add(target)
    await session.commit()
    return target


async def _user(session, email: str, *, keywords: list[str] | None = None) -> User:
    user = User(email=email, password_hash="x", is_verified=True, status="active")
    session.add(user)
    await session.commit()
    for keyword in keywords or []:
        session.add(UserKeyword(user_id=user.id, keyword=keyword, enabled=True))
    await session.commit()
    return user


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "ok"):
        self.status_code = status_code
        self.text = text


class _FakeClients:
    """桩 httpx.AsyncClient：按 URL 排队返回响应，并记录每次 POST。"""

    def __init__(self, outcomes: dict[str, list]):
        self.outcomes = outcomes
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, json: dict = None, headers: dict = None):
        queue = self.outcomes.get(url) or [_FakeResponse(500)]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        self.calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": self.kwargs.get("timeout")}
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _stub_post(monkeypatch, outcomes) -> _FakeClients:
    fake = _FakeClients(outcomes)
    monkeypatch.setattr(webhooks_module.httpx, "AsyncClient", fake)
    return fake


# ---------- token 与 RSS 渲染 ----------


def test_new_feed_token_is_urlsafe_and_unique():
    tokens = {feeds_module.new_feed_token() for _ in range(20)}
    assert len(tokens) == 20
    for token in tokens:
        assert len(token) >= 32
        assert not set(token) & {"=", "+", "/"}


def test_render_rss_escapes_and_has_required_fields():
    xml = feeds_module.render_rss(
        {
            "title": "任务 <测试> & 引号",
            "link": "https://anisignal.test/contents",
            "description": "监控任务描述",
        },
        [
            {
                "title": "标题<脚本>",
                "link": "https://www.bilibili.com/video/BV1?a=1&b=2",
                "description": "正文 & 摘要",
                "source": "B站源",
                "published_at": utcnow().replace(microsecond=0),
                "guid": "anisignal-content-1",
            }
        ],
    )
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert '<rss version="2.0">' in xml
    assert "<script>" not in xml
    assert "标题&lt;脚本&gt;" in xml
    assert "任务 &lt;测试&gt; &amp; 引号" in xml
    assert "&amp;b=2" in xml  # 链接里的 & 必须转义，否则读者解析 XML 会失败
    assert "<source>B站源</source>" in xml
    assert '<guid isPermaLink="false">anisignal-content-1</guid>' in xml
    assert xml.count("<item>") == 1


async def test_ensure_rotate_and_revoke_token(session):
    target = await _target(session)
    first = await feeds_module.ensure_feed_token(session, "target", target.id)
    first_token = first.token
    assert first.status == "active" and first.generation == 1
    # 重复 ensure 不换 token
    assert (await feeds_module.ensure_feed_token(session, "target", target.id)).token == first_token

    rotated = await feeds_module.rotate_feed_token(session, "target", target.id)
    assert rotated.token != first_token
    assert rotated.generation == 2
    assert await feeds_module.resolve_feed_token(session, "target", target.id, first_token) is None
    assert await feeds_module.resolve_feed_token(session, "target", target.id, rotated.token) is not None

    revoked = await feeds_module.revoke_feed_token(session, "target", target.id)
    assert revoked.status == "revoked" and revoked.revoked_at is not None
    assert await feeds_module.resolve_feed_token(session, "target", target.id, rotated.token) is None

    # 撤销过的 feed 重建后恢复，并拿到新地址
    restored = await feeds_module.rotate_feed_token(session, "target", target.id)
    assert restored.status == "active" and restored.generation == 3
    assert await feeds_module.resolve_feed_token(session, "target", target.id, restored.token) is not None


async def test_resolve_rejects_wrong_kind_and_bad_token(session):
    target = await _target(session)
    token = await feeds_module.ensure_feed_token(session, "target", target.id)
    assert await feeds_module.resolve_feed_token(session, "event", target.id, token.token) is None
    assert await feeds_module.resolve_feed_token(session, "target", target.id, "") is None
    assert await feeds_module.resolve_feed_token(session, "target", target.id, "nope") is None
    assert await feeds_module.resolve_feed_token(session, "target", 4242, token.token) is None


async def test_one_token_per_feed(session):
    target = await _target(session)
    await feeds_module.ensure_feed_token(session, "target", target.id)
    await feeds_module.ensure_feed_token(session, "event", target.id)
    rows = list((await session.execute(select(FeedToken))).scalars())
    assert len(rows) == 2
    assert {row.kind for row in rows} == {"target", "event"}


# ---------- feed 端点 ----------


async def test_target_feed_returns_only_that_target_hits(client, session):
    target = await _target(session)
    await _target(session, value="碧蓝航线", display_name="碧蓝航线监控")
    mine = await _content(session, external_id="BVmine", title="鸣潮新版本", keywords=["鸣潮"])
    await _content(session, external_id="BVother", title="碧蓝航线活动", keywords=["碧蓝航线"])
    token = await feeds_module.ensure_feed_token(session, "target", target.id)

    resp = await client.get(f"/api/feeds/target/{target.id}", params={"token": token.token})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/rss+xml")
    xml = resp.text
    assert "鸣潮新版本" in xml
    assert "碧蓝航线活动" not in xml
    assert f"https://www.bilibili.com/video/{mine.external_id}" in xml
    assert "<source>B站源</source>" in xml


async def test_target_feed_skips_hidden_and_ignored(client, session):
    target = await _target(session)
    await _content(
        session, external_id="BVignored", title="鸣潮灌水", keywords=["鸣潮"], intent_tier="ignore"
    )
    await _content(
        session, external_id="BVhidden", title="鸣潮下架", keywords=["鸣潮"], hidden=True
    )
    await _content(session, external_id="BVkept", title="鸣潮官宣", keywords=["鸣潮"])
    token = await feeds_module.ensure_feed_token(session, "target", target.id)

    xml = (await client.get(f"/api/feeds/target/{target.id}", params={"token": token.token})).text
    assert "鸣潮官宣" in xml
    assert "鸣潮灌水" not in xml
    assert "鸣潮下架" not in xml


async def test_event_feed_lists_evidences(client, session):
    first = await _content(
        session,
        external_id="BVe1",
        title="鸣潮预告",
        keywords=["鸣潮"],
        published_at=utcnow() - timedelta(hours=1),
    )
    second = await _content(
        session,
        external_id="BVe2",
        title="鸣潮开服",
        keywords=["鸣潮"],
        published_at=utcnow() - timedelta(days=2),
    )
    dropped = await _content(session, external_id="BVe3", title="鸣潮误报", keywords=["鸣潮"])
    event = Event(title="鸣潮新版本事件", category="announce", community="wuwa")
    session.add(event)
    await session.flush()
    session.add_all(
        [
            Evidence(event_id=event.id, raw_content_id=first.id, role="support"),
            Evidence(event_id=event.id, raw_content_id=second.id, role="support"),
            Evidence(event_id=event.id, raw_content_id=dropped.id, role="excluded"),
        ]
    )
    await session.commit()
    token = await feeds_module.ensure_feed_token(session, "event", event.id)

    resp = await client.get(f"/api/feeds/event/{event.id}", params={"token": token.token})
    assert resp.status_code == 200
    xml = resp.text
    assert "鸣潮新版本事件" in xml  # 频道标题
    assert "鸣潮预告" in xml and "鸣潮开服" in xml
    assert "鸣潮误报" not in xml  # 人工排除的证据不进订阅源
    assert xml.index("鸣潮预告") < xml.index("鸣潮开服")  # 最新证据在前


async def test_feed_requires_valid_token(client, session):
    target = await _target(session)
    assert (await client.get(f"/api/feeds/target/{target.id}")).status_code == 401

    resp = await client.get(f"/api/feeds/target/{target.id}", params={"token": "wrong"})
    assert resp.status_code == 404  # 不泄露 feed 是否存在

    revoked = await feeds_module.revoke_feed_token(session, "target", target.id)
    resp = await client.get(
        f"/api/feeds/target/{target.id}", params={"token": revoked.token}
    )
    assert resp.status_code == 404

    assert (await client.get("/api/feeds/unknown/1", params={"token": "x"})).status_code == 404


async def test_feed_404_for_missing_target(client, session):
    token = await feeds_module.ensure_feed_token(session, "target", 999)
    resp = await client.get("/api/feeds/target/999", params={"token": token.token})
    assert resp.status_code == 404


# ---------- 管理端 ----------


async def test_admin_lists_feeds_and_manages_tokens(client, session, admin_headers):
    target = await _target(session)
    event = Event(title="事件甲", category="announce", community="wuwa")
    session.add(event)
    await session.commit()

    resp = await client.get("/api/admin/feeds", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"] == {"target": 1, "event": 1}
    by_kind = {(item["kind"], item["ref_id"]): item for item in body["items"]}
    target_feed = by_kind[("target", target.id)]
    assert target_feed["title"] == "鸣潮"
    assert target_feed["feed_url"].endswith(
        f"/api/feeds/target/{target.id}?token={target_feed['token']}"
    )
    assert target_feed["token_status"] == "active"
    assert ("event", event.id) in by_kind

    # token 已自动落库（每个任务/事件都"自带"一份只读 feed）
    stored = await feeds_module.get_feed_token(session, "target", target.id)
    assert stored is not None and stored.token == target_feed["token"]

    # 重建：返回新地址，旧地址失效
    resp = await client.post(f"/api/admin/feeds/target/{target.id}/token", headers=admin_headers)
    assert resp.status_code == 200
    new_token = resp.json()["token"]
    assert new_token != target_feed["token"]
    assert resp.json()["generation"] == 2
    assert (
        await client.get(
            f"/api/feeds/target/{target.id}", params={"token": target_feed["token"]}
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/feeds/target/{target.id}", params={"token": new_token})
    ).status_code == 200

    # 撤销
    resp = await client.delete(f"/api/admin/feeds/target/{target.id}/token", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["token_status"] == "revoked"
    assert (
        await client.get(f"/api/feeds/target/{target.id}", params={"token": new_token})
    ).status_code == 404

    assert (await client.post("/api/admin/feeds/other/1/token", headers=admin_headers)).status_code == 400


async def test_admin_feed_search_filters(client, session, admin_headers):
    await _target(session, value="鸣潮", display_name="鸣潮监控")
    await _target(session, value="碧蓝航线")
    resp = await client.get(
        "/api/admin/feeds", params={"kind": "target", "q": "碧蓝"}, headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["target_value"] == "碧蓝航线"


def test_resolve_base_url_prefers_configuration(monkeypatch):
    class _Request:
        base_url = "http://testserver/"

    configured = get_settings().public_base_url.strip().rstrip("/")
    assert feeds_module.resolve_base_url(_Request()) == configured

    monkeypatch.setattr(get_settings(), "public_base_url", "")
    assert feeds_module.resolve_base_url(_Request()) == "http://testserver"


# ---------- Webhook ----------


def test_is_safe_webhook_url_blocks_internal_targets():
    assert webhooks_module.is_safe_webhook_url("https://hooks.example.com/n8n")
    assert webhooks_module.is_safe_webhook_url("http://example.com:8080/hook")
    assert not webhooks_module.is_safe_webhook_url("http://localhost:9000/hook")
    assert not webhooks_module.is_safe_webhook_url("http://127.0.0.1/hook")
    assert not webhooks_module.is_safe_webhook_url("http://192.168.1.10/hook")
    assert not webhooks_module.is_safe_webhook_url("http://10.0.0.5/hook")
    assert not webhooks_module.is_safe_webhook_url("http://[::1]/hook")
    assert not webhooks_module.is_safe_webhook_url("file:///etc/passwd")
    assert not webhooks_module.is_safe_webhook_url("ftp://example.com/hook")
    assert not webhooks_module.is_safe_webhook_url("")
    assert not webhooks_module.is_safe_webhook_url("not-a-url")


def test_build_payload_is_n8n_compatible():
    payload = webhooks_module.build_payload(
        {
            "id": 7,
            "title": "鸣潮新版本官宣",
            "url": "https://www.bilibili.com/video/BV1",
            "summary": "官方发布新版本预告。",
            "source": "鸣潮官方",
            "platform": "bilibili",
            "community": "wuwa",
            "keywords": ["鸣潮"],
            "tier": "important",
            "published_at": utcnow().replace(microsecond=0),
        }
    )
    assert set(payload) >= {"title", "link", "content", "source", "timestamp", "tier"}
    assert payload["title"] == "鸣潮新版本官宣"
    assert payload["link"] == "https://www.bilibili.com/video/BV1"
    assert payload["content"] == "官方发布新版本预告。"
    assert payload["source"] == "鸣潮官方"
    assert payload["tier"] == "important"
    assert payload["timestamp"].endswith("+00:00")
    assert payload["id"] == 7 and payload["community"] == "wuwa"


async def test_dispatch_posts_hits_and_records_delivery(session, monkeypatch):
    user = await _user(session, "hook@t.com", keywords=["鸣潮"])
    hook = WebhookConfig(user_id=user.id, url="https://hooks.example.com/n8n")
    session.add(hook)
    await session.commit()
    content = await _content(
        session,
        external_id="BVhook1",
        title="鸣潮新角色官宣",
        keywords=["鸣潮"],
        published_at=utcnow() - timedelta(hours=1),
    )
    fake = _stub_post(monkeypatch, {"https://hooks.example.com/n8n": [_FakeResponse(200)]})

    stats = await webhooks_module.run_webhook_dispatch(session)
    assert stats == {"users": 1, "sent": 1, "failed": 0, "skipped": 0}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == hook.url
    assert call["timeout"] == 10.0  # webhook 超时 10 秒
    assert call["json"]["title"] == "鸣潮新角色官宣"
    assert call["json"]["tier"] == "normal"

    rows = list((await session.execute(select(WebhookDelivery))).scalars())
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert rows[0].attempts == 1
    assert rows[0].response_code == 200
    assert rows[0].dedupe_key == f"webhook:{hook.id}:content:{content.id}"
    assert rows[0].sent_at is not None
    assert rows[0].payload["link"] == call["json"]["link"]

    # 同一内容不重复打扰
    fake.calls.clear()
    stats = await webhooks_module.run_webhook_dispatch(session)
    assert stats == {"users": 1, "sent": 0, "failed": 0, "skipped": 1}
    assert fake.calls == []


async def test_dispatch_retries_three_times_then_records_failure(session, monkeypatch):
    user = await _user(session, "retry@t.com", keywords=["鸣潮"])
    hook = WebhookConfig(user_id=user.id, url="https://hooks.example.com/n8n")
    session.add(hook)
    await session.commit()
    await _content(
        session,
        external_id="BVretry",
        title="鸣潮限定活动",
        keywords=["鸣潮"],
        published_at=utcnow() - timedelta(hours=1),
    )
    fake = _stub_post(
        monkeypatch, {"https://hooks.example.com/n8n": [_FakeResponse(500, "boom")]}
    )

    stats = await webhooks_module.run_webhook_dispatch(session)
    assert stats == {"users": 1, "sent": 0, "failed": 1, "skipped": 0}
    assert len(fake.calls) == 3  # 失败重试 3 次

    row = await session.scalar(select(WebhookDelivery))
    assert row.status == "failed"
    assert row.attempts == 3
    assert row.response_code == 500
    assert "HTTP 500" in row.error

    # 已达重试上限：下一轮不再打这个地址
    fake.calls.clear()
    stats = await webhooks_module.run_webhook_dispatch(session)
    assert stats == {"users": 1, "sent": 0, "failed": 0, "skipped": 1}
    assert fake.calls == []


async def test_dispatch_retries_network_errors(session, monkeypatch):
    user = await _user(session, "neterr@t.com", keywords=["鸣潮"])
    hook = WebhookConfig(user_id=user.id, url="https://hooks.example.com/n8n")
    session.add(hook)
    await session.commit()
    await _content(
        session,
        external_id="BVneterr",
        title="鸣潮维护公告",
        keywords=["鸣潮"],
        published_at=utcnow() - timedelta(hours=1),
    )
    fake = _stub_post(
        monkeypatch, {"https://hooks.example.com/n8n": [RuntimeError("connection reset")]}
    )
    stats = await webhooks_module.run_webhook_dispatch(session)
    assert stats["failed"] == 1
    assert len(fake.calls) == 3
    row = await session.scalar(select(WebhookDelivery))
    assert row.status == "failed" and row.response_code is None
    assert "connection reset" in row.error


async def test_dispatch_honours_tier_filter_and_ignored_tier(session, monkeypatch):
    user = await _user(session, "tier@t.com", keywords=["鸣潮"])
    hook = WebhookConfig(
        user_id=user.id, url="https://hooks.example.com/n8n", tier_filter="important"
    )
    session.add(hook)
    await session.commit()
    await _content(
        session,
        external_id="BVnormal",
        title="鸣潮二创展示",
        keywords=["鸣潮"],
        published_at=utcnow() - timedelta(hours=1),
    )
    await _content(
        session,
        external_id="BVimportant",
        title="鸣潮重大更新官宣",
        keywords=["鸣潮"],
        published_at=utcnow() - timedelta(hours=2),
        intent_tier="important",
    )
    await _content(
        session,
        external_id="BVignored",
        title="鸣潮无关灌水",
        keywords=["鸣潮"],
        published_at=utcnow() - timedelta(hours=3),
        intent_tier="ignore",
    )
    fake = _stub_post(monkeypatch, {"https://hooks.example.com/n8n": [_FakeResponse(200)]})

    stats = await webhooks_module.run_webhook_dispatch(session)
    assert stats == {"users": 1, "sent": 1, "failed": 0, "skipped": 1}
    assert len(fake.calls) == 1
    assert fake.calls[0]["json"]["tier"] == "important"


async def test_dispatch_skips_users_without_keywords(session, monkeypatch):
    user = await _user(session, "nokw@t.com")
    hook = WebhookConfig(user_id=user.id, url="https://hooks.example.com/n8n")
    session.add(hook)
    await session.commit()
    fake = _stub_post(monkeypatch, {})
    # 配了渠道但没有启用中的关键词：不推送
    assert await webhooks_module.run_webhook_dispatch(session) == {
        "users": 1, "sent": 0, "failed": 0, "skipped": 0
    }
    assert fake.calls == []

    # 没有任何 webhook 时零成本返回（连用户都不扫）
    await session.delete(hook)
    await session.commit()
    assert await webhooks_module.run_webhook_dispatch(session) == {
        "users": 0, "sent": 0, "failed": 0, "skipped": 0
    }
    assert fake.calls == []


async def test_send_test_records_delivery(session, monkeypatch):
    user = await _user(session, "test@t.com")
    hook = WebhookConfig(user_id=user.id, url="https://hooks.example.com/n8n")
    session.add(hook)
    await session.commit()
    _stub_post(monkeypatch, {"https://hooks.example.com/n8n": [_FakeResponse(204)]})
    row = await webhooks_module.send_test(session, hook)
    assert row.status == "sent" and row.kind == "test" and row.dedupe_key is None
    assert row.payload["title"] == "AniSignal Webhook 测试"


# ---------- 账号中心 webhook CRUD ----------


async def _register(client, session, email="hookuser@t.com"):
    from tests.test_account_api import _register_and_login

    await _register_and_login(client, session, email=email)
    return await session.scalar(select(User).where(User.email == email))


async def test_account_webhook_crud_and_test(client, session, monkeypatch):
    await _register(client, session)

    resp = await client.post(
        "/api/account/webhooks", json={"url": "https://hooks.example.com/n8n", "name": "n8n"}
    )
    assert resp.status_code == 201
    hook_id = resp.json()["id"]
    assert resp.json()["tier_filter"] == "all"

    # 非法地址被拒
    assert (await client.post("/api/account/webhooks", json={"url": "http://127.0.0.1:9/x"})).status_code == 400
    assert (await client.post("/api/account/webhooks", json={"url": ""})).status_code == 400

    # 测试投递 + 列表里的最近记录
    _stub_post(monkeypatch, {"https://hooks.example.com/n8n": [_FakeResponse(200)]})
    resp = await client.post(f"/api/account/webhooks/{hook_id}/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"
    resp = await client.get("/api/account/webhooks")
    assert resp.status_code == 200
    body = resp.json()
    assert [item["id"] for item in body["items"]] == [hook_id]
    assert body["deliveries"][0]["status"] == "sent"
    assert body["deliveries"][0]["kind"] == "test"
    assert len(body["tier_options"]) == 2

    # 改推送范围与启停
    resp = await client.patch(
        f"/api/account/webhooks/{hook_id}", json={"tier_filter": "all", "enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["tier_filter"] == "all" and resp.json()["enabled"] is False
    assert (
        await client.patch(f"/api/account/webhooks/{hook_id}", json={"tier_filter": "sometimes"})
    ).status_code == 400

    # 别人的 webhook 动不了
    stranger = await _user(session, "stranger@t.com")
    session.add(WebhookConfig(user_id=stranger.id, url="https://hooks.example.com/other"))
    await session.commit()
    stranger_hook_id = await session.scalar(
        select(WebhookConfig.id).where(WebhookConfig.user_id == stranger.id)
    )
    assert (await client.patch(f"/api/account/webhooks/{stranger_hook_id}", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/api/account/webhooks/{stranger_hook_id}")).status_code == 404

    resp = await client.delete(f"/api/account/webhooks/{hook_id}")
    assert resp.status_code == 204
    resp = await client.get("/api/account/webhooks")
    assert resp.json()["items"] == []


async def test_admin_lists_webhook_deliveries_and_retries(client, session, admin_headers, monkeypatch):
    user = await _user(session, "adminview@t.com")
    hook = WebhookConfig(user_id=user.id, url="https://hooks.example.com/n8n", name="n8n 主线")
    session.add(hook)
    await session.commit()
    session.add(
        WebhookDelivery(
            webhook_id=hook.id,
            user_id=user.id,
            kind="hit",
            title="鸣潮官宣",
            payload={"title": "鸣潮官宣", "link": "https://www.bilibili.com/x"},
            dedupe_key=f"webhook:{hook.id}:content:1",
            status="failed",
            attempts=3,
            error="HTTP 500",
        )
    )
    await session.commit()

    resp = await client.get("/api/admin/webhooks", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["owner_email"] == "adminview@t.com"
    assert body["items"][0]["failed"] == 1
    assert body["items"][0]["sent"] == 0

    resp = await client.get(
        "/api/admin/webhooks/deliveries",
        params={"delivery_status": "failed"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["webhook_name"] == "n8n 主线"
    assert row["attempts"] == 3 and row["error"] == "HTTP 500"

    # 管理端重推：用已存 payload 再发一次
    fake = _stub_post(monkeypatch, {"https://hooks.example.com/n8n": [_FakeResponse(200)]})
    resp = await client.post(
        f"/api/admin/webhooks/deliveries/{row['id']}/retry", headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"
    assert len(fake.calls) == 1
    assert fake.calls[0]["json"]["title"] == "鸣潮官宣"

    # 记录已更新为成功，失败过滤后查不到
    resp = await client.get(
        "/api/admin/webhooks/deliveries",
        params={"delivery_status": "failed"},
        headers=admin_headers,
    )
    assert resp.json()["total"] == 0


async def test_admin_output_requires_token(client):
    assert (await client.get("/api/admin/webhooks")).status_code == 422
    assert (await client.get("/api/admin/feeds")).status_code == 422
    resp = await client.get("/api/admin/feeds", headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401
