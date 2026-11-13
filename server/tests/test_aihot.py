"""检查原生统一资讯网关边界：图片相对路径重写、信源健康合并、故障不降级与撤稿拦截。"""

import httpx
import pytest

from app.clock import utcnow
from app.config import get_settings
from app.models import CollectTarget
from app.services import aihot, bot
from tests.test_bot import target


@pytest.fixture
def engine_config(monkeypatch):
    monkeypatch.setattr(get_settings(), "news_unified_enabled", True)


def test_media_rewrite_preserves_external_original_urls():
    external = "https://example.org/api/img-proxy?article=1"
    assert aihot.public_media(external) == external
    assert aihot.public_media("/api/img-proxy?u=fixture") == "/api/ai-news/media?u=fixture"
    assert aihot.public_media('<img src="/api/img-proxy?u=fixture">') == '<img src="/api/ai-news/media?u=fixture">'


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
    authors = [{**common, "id": f"anisignal-bilibili-{i}", "name": f"历史作者{i}", "platform": "bridge"} for i in range(15)]
    rss = [{**common, "id": f"rss-{i}", "name": f"RSS {i}", "platform": "rss",
            "status": "delayed" if i == 0 else "pending" if i == 1 else "active"} for i in range(18)]

    async def sources():
        return authors + rss

    async def feed(**kwargs):
        return {"items": [], "total": 0, "topics": [], "sources": await sources(), "engine": "anisignal"}

    monkeypatch.setattr(aihot, "sources", sources)
    monkeypatch.setattr(aihot, "feed", feed)
    response = await client.get(path)
    assert response.status_code == 200
    rows = response.json()["sources"]
    assert len(rows) == 20
    assert {row["platform"] for row in rows} == {"bilibili", "x", "rss"}
    assert [row["name"] for row in rows[:2]] == ["B站 · Codex", "X · OpenAI"]
    assert rows[1]["status"] == "delayed"
    assert [row["status"] for row in rows[2:4]] == ["delayed", "pending"]
    assert {row["name"] for row in rows[2:]} == {f"RSS {i}" for i in range(18)}


async def test_publication_failure_never_falls_back_to_legacy(client, monkeypatch, engine_config):
    async def unavailable(*args, **kwargs):
        from fastapi import HTTPException
        raise HTTPException(503, "engine unavailable")

    monkeypatch.setattr(aihot, "feed", unavailable)
    assert (await client.get("/api/ai-news/events")).status_code == 503
    assert (await client.get("/api/ai-news/engine/admin/settings")).status_code == 404


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
