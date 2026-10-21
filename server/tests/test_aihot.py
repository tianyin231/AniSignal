"""检查真实切换边界：不降级漏发、桥接失败不丢版本、撤稿和修订使 Bot 快照失效。"""
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.clock import utcnow
from app.models import CollectTarget, RawContent, ContentVersion
from app.services import aihot, aihot_bridge, bot
from app.services.settings import get_setting
from tests.test_bot import target


def test_media_rewrite_preserves_external_original_urls():
    external = "https://example.org/api/img-proxy?article=1"
    assert aihot.public_media(external) == external
    assert aihot.public_media('/api/img-proxy?u=fixture') == '/api/ai-news/media?u=fixture'
    assert aihot.public_media('<img src="/api/img-proxy?u=fixture">') == '<img src="/api/ai-news/media?u=fixture">'


async def test_public_source_list_excludes_isolated_sources(monkeypatch, engine_config):
    async def listing(*args, **kwargs):
        common = {"kind": "rss", "enabled": True, "interval_minutes": 60}
        return {"totals": {"total": 2}, "rows": [
            {**common, "id": "public", "name": "公开源", "participation_mode": "editorial"},
            {**common, "id": "private", "name": "隔离源", "participation_mode": "isolated"}]}
    monkeypatch.setattr(aihot, "request", listing)
    assert [r["id"] for r in await aihot.sources()] == ["public"]


@pytest.fixture
def engine_config(monkeypatch):
    config = SimpleNamespace(aihot_url="http://engine.invalid", aihot_token="fixture-bridge-token",
        aihot_read_enabled=True, aihot_bot_after="2026-10-20T12:00:00Z")
    monkeypatch.setattr(aihot, "get_settings", lambda: config)
    return config


@pytest.mark.parametrize("path", ["/api/ai-news", "/api/ai-news/events", "/api/ai-news/sources"])
async def test_source_health_uses_collection_targets_and_native_rss(client, session, monkeypatch, engine_config, path):
    now = utcnow()
    session.add_all([
        CollectTarget(platform="bilibili", target_type="keyword_search", target_value="Codex", display_name="B站 · Codex",
                      community="ai", enabled=True, last_success_at=now),
        CollectTarget(platform="x", target_type="account_dynamics", target_value="OpenAI", display_name="X · OpenAI",
                      community="ai", enabled=True, last_success_at=now, consecutive_failures=1),
        CollectTarget(platform="x", target_type="account_dynamics", target_value="paused", community="ai", enabled=False),
        CollectTarget(platform="bilibili", target_type="keyword_search", target_value="鸣潮", community="wuwa", enabled=True),
    ])
    await session.commit()
    common = {"status": "active", "last_success_at": None, "interval_minutes": 30}
    authors = [{**common, "id": f"anisignal-bilibili-{i}", "name": f"历史作者{i}", "platform": "bridge"} for i in range(1529)]
    rss = [{**common, "id": f"rss-{i}", "name": f"RSS {i}", "platform": "rss",
            "status": "delayed" if i == 0 else "pending" if i == 1 else "active"} for i in range(18)]

    async def sources():
        return authors + rss

    async def feed(**kwargs):
        return {"items": [], "total": 0, "topics": [], "sources": await sources(), "engine": "aihot"}

    monkeypatch.setattr(aihot, "sources", sources)
    monkeypatch.setattr(aihot, "feed", feed)
    response = await client.get(path)
    assert response.status_code == 200
    rows = response.json()["sources"]
    assert len(rows) == 20  # 两个启用的监控目标 + 18 个原生 RSS，不为历史作者逐个生成健康点。
    assert {row["platform"] for row in rows} == {"bilibili", "x", "rss"}
    assert [row["name"] for row in rows[:2]] == ["B站 · Codex", "X · OpenAI"]
    assert rows[1]["status"] == "delayed"  # 使用真实采集状态，不继承桥接作者的 active。
    assert [row["status"] for row in rows[2:4]] == ["delayed", "pending"]
    assert {row["name"] for row in rows[2:]} == {f"RSS {i}" for i in range(18)}


async def test_publication_failure_never_falls_back_to_legacy(client, monkeypatch, engine_config):
    async def unavailable(*args, **kwargs):
        from fastapi import HTTPException
        raise HTTPException(503, "engine unavailable")
    monkeypatch.setattr(aihot, "request", unavailable)
    assert (await client.get("/api/ai-news/events")).status_code == 503
    assert (await client.get("/api/ai-news/engine/admin/settings")).status_code == 404


async def test_bot_uses_original_selected_revision_and_cutover(monkeypatch, engine_config):
    calls = []
    now = utcnow()
    async def original(path, **kwargs):
        calls.append((path, kwargs))
        return [{"id": "native", "title": "模型发布", "original_title": None, "summary": "已开放测试。", "url": "https://example.org/news",
            "revision": 3, "fact_id": 8, "published_at": aihot.iso(now), "updated_at": aihot.iso(now),
            "channel": "news", "first_party": True, "source_name": "研究机构"}]
    monkeypatch.setattr(aihot, "request", original)
    items = await aihot.bot_items([], now-timedelta(days=1), now, collected_after=now-timedelta(minutes=5))
    assert items[0]["id"] == "aihot:native" and items[0]["news"]["revision"] == 3
    assert calls[0][1]["params"]["cutover"] == engine_config.aihot_bot_after
    await aihot.bot_items([], now-timedelta(days=1), now)
    assert "cutover" not in calls[-1][1]["params"]


async def test_bridge_failure_keeps_watermark_and_retries_same_version(session, monkeypatch, engine_config):
    raw = RawContent(platform="bilibili", external_id="bridge-fixture", content_type="video", community="ai",
        url="https://www.bilibili.com/video/BVfixture", published_at=utcnow())
    session.add(raw)
    await session.flush()
    version = ContentVersion(raw_content_id=raw.id, title="原视频标题", text="视频简介", normalized_json={"media": []})
    session.add(version)
    await session.commit()
    attempts = []
    async def unavailable(path, **kwargs):
        attempts.append(kwargs["body"])
        raise RuntimeError("unavailable")
    monkeypatch.setattr(aihot, "request", unavailable)
    with pytest.raises(RuntimeError):
        await aihot_bridge.sync(session)
    assert (await get_setting(session, "aihot_bridge"))["cursor"] == 0
    async def accepted(path, **kwargs):
        attempts.append(kwargs["body"])
        return {"articleId": kwargs["body"]["id"]}
    monkeypatch.setattr(aihot, "request", accepted)
    result = await aihot_bridge.sync(session)
    assert result["cursor"] == version.id and attempts[0] == attempts[1]
    assert attempts[0]["backfill"] == "anisignal-initial-import"


async def test_bridge_never_replaces_live_revision_with_old_history(session, monkeypatch, engine_config):
    raw = RawContent(platform="x", external_id="bridge-edited", content_type="post", community="ai",
        url="https://x.com/example/status/123", published_at=utcnow())
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, title="旧标题", text="旧正文"))
    await session.commit()
    from app.services.settings import set_setting
    await set_setting(session, "aihot_bridge", {"cursor": 0, "live_cursor": 1, "cutover": 1})
    session.add(ContentVersion(raw_content_id=raw.id, version=2, title="新标题", text="修订正文"))
    await session.commit()
    sent = []
    async def accepted(path, **kwargs):
        sent.append(kwargs["body"])
        return {}
    monkeypatch.setattr(aihot, "request", accepted)
    await aihot_bridge.sync(session)
    assert [i["title"] for i in sent] == ["新标题"]


@pytest.mark.parametrize("status,hidden,approved", [
    ("pending", True, None), ("stale", True, None),
    ("approved", False, True), ("rejected", True, False), ("manual", True, True),
])
def test_bridge_distinguishes_pending_from_withdrawal_and_keeps_current_review(status, hidden, approved):
    now = utcnow()
    raw = RawContent(id=42, platform="bilibili", external_id="review-fixture", community="ai", content_type="video",
        url="https://www.bilibili.com/video/BVreview", content_hash="current", hidden=hidden,
        published_at=now, collected_at=now)
    version = ContentVersion(id=9, version=2, title="原始标题", text="视频简介")
    review = None if status == "pending" else SimpleNamespace(
        relevant=True, promotional=False, violation=False, model="original-model", updated_at=now,
        news={"content_hash": "current", "content_version": 1 if status == "stale" else 2,
              "newsworthy": status != "rejected", "headline": "原站已审标题", "summary": "原站已审摘要", "topics": ["Codex"]})
    body = aihot_bridge.payload(raw, version, None, cutover=10, review=review)
    assert body["hidden"] is (hidden and approved is not None)
    if approved is None:
        assert body["review"] is None
    else:
        assert body["review"]["approved"] is approved
        assert body["review"]["headline"] == "原站已审标题"
        assert body["review"]["summary"] == "原站已审摘要"
        assert body["review"]["model"] == "original-model"


async def test_bot_native_snapshot_is_revalidated_and_outage_defers(session, monkeypatch, engine_config):
    now = utcnow()
    private = await target(session, community="ai", daily_enabled=False, updates_enabled=True, quiet_start=0, quiet_end=0)
    item = {"id": "aihot:native", "community": "ai", "title": "模型发布", "summary": "已发布", "text": "已发布",
        "content_type": "rss_item", "platform": "rss", "published_at": now.isoformat(),
        "news": {"engine": "aihot", "engine_id": "native", "revision": 2, "kind": "report", "headline": "模型发布", "summary": "已发布"}}
    row = await bot.create_delivery(session, private, "updates", "消息", "native-snapshot",
        brief={"mode": "single", "items": [item], "news_dedupe_checked": True})
    async def outage(items):
        raise httpx.ConnectError("outage")
    monkeypatch.setattr(aihot, "validate_bot_items", outage)
    assert not await bot.prepare_delivery(session, row, target=private, now=now)
    assert row.status == "pending"
    async def withdrawn(items):
        return set()
    monkeypatch.setattr(aihot, "validate_bot_items", withdrawn)
    assert not await bot.prepare_delivery(session, row, target=private, now=now)
    assert row.status == "cancelled"
