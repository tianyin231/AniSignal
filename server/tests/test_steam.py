"""Steam 官方公告与实时遥测集成测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.collectors.base import CollectorError
from app.collectors.steam import (
    STEAM_APPDETAILS_URL,
    STEAM_NEWS_URL,
    STEAM_ONLINE_URL,
    SteamCollector,
    _DETAIL_CACHE,
)
from app.models import CollectTarget, ContentTranslation, RawContent, SteamAppSnapshot
from app.models.enums import Identity, Platform
from app.parsers.steam import clean_steam_contents, extract_app_id, parse_notice
from app.services.ai import translate_content
from app.services.collect import run_collection
from app.services.ingest import reparse_contents
from app.services.retention import run_retention
from app.services.steam import get_steam_pulse, save_steam_snapshot, thin_steam_snapshots


@pytest.fixture(autouse=True)
def _clear_steam_detail_cache():
    _DETAIL_CACHE.clear()
    yield
    _DETAIL_CACHE.clear()


def test_extract_app_id_accepts_numeric_and_store_urls():
    assert extract_app_id("3513350") == "3513350"
    assert extract_app_id(" 003513350 ") == "3513350"
    assert extract_app_id("https://store.steampowered.com/app/3513350/Wuthering_Waves/?l=schinese") == "3513350"
    assert extract_app_id("https://steamcommunity.com/app/4162040/announcements/") == "4162040"

    for bad in [
        "",
        "0",
        "-1",
        "abc",
        "9999999999",
        "https://example.com/app/3513350/",
        "https://store.steampowered.com/news/app/3513350/",
        "javascript:alert(1)",
    ]:
        with pytest.raises(ValueError):
            extract_app_id(bad)


def test_clean_steam_contents_and_parse_notice():
    raw_body = (
        "[h2]Version 2.4 Update[/h2]\n"
        "[img]{STEAM_CLAN_IMAGE}/45123456/banner.png[/img]\n"
        '<img src="https://clan.fastly.steamstatic.com/images/45123456/pic2.jpg" />'
        '<img src="javascript:alert(1)" />'
        "[*]New story chapter\n"
        "[url=https://wutheringwaves.kurogames.com]Official Site[/url]\n"
        "<script>alert('xss')</script><p>Maintenance begins soon.</p>"
    )
    text, media = clean_steam_contents(raw_body)
    assert media == [
        "https://clan.fastly.steamstatic.com/images/45123456/banner.png",
        "https://clan.fastly.steamstatic.com/images/45123456/pic2.jpg",
    ]
    assert text is not None
    assert "Version 2.4 Update" in text
    assert "• New story chapter" in text
    assert "Official Site" in text
    assert "Maintenance begins soon." in text
    assert "alert('xss')" not in text
    assert "<script>" not in text
    assert "{STEAM_CLAN_IMAGE}" not in text

    parsed = parse_notice({
        "app_id": 3513350,
        "app_name": "鸣潮",
        "item": {
            "gid": "532098765432109876",
            "title": "Version 2.4 Maintenance Notice",
            "contents": raw_body,
            "date": 1760000000,
            "feedname": "steam_community_announcements",
        },
    })
    assert parsed.platform == Platform.STEAM
    assert parsed.content_type.value == "notice"
    assert parsed.external_id == "3513350:532098765432109876"
    assert parsed.url == "https://store.steampowered.com/news/app/3513350/view/532098765432109876"
    assert parsed.author is not None
    assert parsed.author.identity == Identity.OFFICIAL
    assert parsed.author.name == "鸣潮 · Steam"
    assert parsed.extra["region"] == "global"
    assert len(parsed.media) == 2


@pytest.mark.asyncio
async def test_steam_collector_fetches_news_snapshot_and_caches_details(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_get_json(_client, url, *, params=None, **_kwargs):
        params = dict(params or {})
        calls.append((url, params))
        if url == STEAM_NEWS_URL:
            assert params["feeds"] == "steam_community_announcements"
            assert params["maxlength"] == 0
            return {
                "appnews": {
                    "appid": 3513350,
                    "newsitems": [
                        {
                            "gid": "100001",
                            "title": "Patch 2.4 Notes",
                            "contents": "[p]Hello Rovers[/p]",
                            "date": 1760000000,
                            "feedname": "steam_community_announcements",
                        }
                    ],
                }
            }
        if url == STEAM_APPDETAILS_URL:
            assert params["cc"] == "cn"
            return {
                "3513350": {
                    "success": True,
                    "data": {"name": "鸣潮", "is_free": True},
                }
            }
        if url == STEAM_ONLINE_URL:
            return {"response": {"player_count": 24500, "result": 1}}
        if "/ajaxappreviews/" in url:
            assert params["appids"] == 3513350
            assert params["num_per_page"] == 0
            if params["language"] == "all":
                return {
                    "success": 1,
                    "query_summary": {
                        "total_positive": 18000,
                        "total_negative": 2000,
                        "review_score_desc": "Very Positive",
                    },
                }
            return {
                "success": 1,
                "query_summary": {
                    "total_positive": 4200,
                    "total_negative": 800,
                    "review_score_desc": "Very Positive",
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("app.collectors.steam.get_json", _fake_get_json)

    collector = SteamCollector()
    res1 = await collector.fetch("notices", "3513350", limit=10)
    assert res1.fetch_kind == "steam_notices"
    assert len(res1.items) == 1
    assert res1.items[0]["app_name"] == "鸣潮"
    assert collector.last_snapshot is not None
    assert collector.last_snapshot["player_count"] == 24500
    assert collector.last_snapshot["global_positive"] == 18000
    assert collector.last_snapshot["cn_positive"] == 4200
    assert collector.last_snapshot["price_formatted"] == "免费开玩"

    # 第二轮复用 24h appdetails 缓存，不再请求 appdetails
    calls.clear()
    await collector.fetch("notices", "3513350", limit=10)
    assert not any(url == STEAM_APPDETAILS_URL for url, _ in calls)


@pytest.mark.asyncio
async def test_steam_collector_degrades_when_telemetry_fails(monkeypatch):
    async def _fake_get_json(_client, url, *, params=None, **_kwargs):
        if url == STEAM_NEWS_URL:
            return {
                "appnews": {
                    "appid": 4162040,
                    "newsitems": [
                        {
                            "gid": "200001",
                            "title": "Coming Soon on Steam",
                            "contents": "Wishlist now!",
                            "date": 1760000000,
                            "feedname": "steam_community_announcements",
                        }
                    ],
                }
            }
        raise CollectorError("temporary steam store timeout")

    monkeypatch.setattr("app.collectors.steam.get_json", _fake_get_json)

    collector = SteamCollector()
    res = await collector.fetch("notices", "4162040")
    assert len(res.items) == 1
    assert collector.last_snapshot is None

    with pytest.raises(CollectorError):
        await collector.fetch("keyword_search", "4162040")


@pytest.mark.asyncio
async def test_steam_end_to_end_admin_collection_public_contents_and_pulse(
    client, admin_headers, session_factory, monkeypatch
):
    class _FakeRedis:
        def __init__(self):
            self.store: dict[str, object] = {}
            self.jobs: list[tuple] = []

        async def set(self, key, value, nx=False, px=None, ex=None):
            if nx and key in self.store:
                return False
            self.store[key] = value
            return True

        async def get(self, key):
            return self.store.get(key)

        async def enqueue_job(self, name, *args, **kwargs):
            self.jobs.append((name, args, kwargs))

    fake_redis = _FakeRedis()
    # 1. 管理端支持粘贴 Steam 商店链接，后端自动规范化为纯数字 AppID
    create_resp = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "steam",
            "target_type": "notices",
            "target_value": "https://store.steampowered.com/app/3513350/Wuthering_Waves/",
            "display_name": "鸣潮 · Steam",
            "community": "wuwa",
            "tier": "core",
            "interval_minutes": 30,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    assert created["target_value"] == "3513350"
    target_id = created["id"]

    # 2. 执行采集（patch SessionLocal + SteamCollector HTTP）
    async def _fake_get_json(_client, url, *, params=None, **_kwargs):
        params = dict(params or {})
        if url == STEAM_NEWS_URL:
            return {
                "appnews": {
                    "appid": 3513350,
                    "newsitems": [
                        {
                            "gid": "900001",
                            "title": "Version 2.4 Livestream Recap",
                            "contents": "New Resonator arrives on Steam!",
                            "date": int(utcnow().timestamp()) - 300,
                            "feedname": "steam_community_announcements",
                        }
                    ],
                }
            }
        if url == STEAM_APPDETAILS_URL:
            return {"3513350": {"success": True, "data": {"name": "鸣潮", "is_free": True}}}
        if url == STEAM_ONLINE_URL:
            return {"response": {"player_count": 31200, "result": 1}}
        if "/ajaxappreviews/" in url:
            if params["language"] == "all":
                return {
                    "success": 1,
                    "query_summary": {
                        "total_positive": 9000,
                        "total_negative": 1000,
                        "review_score_desc": "Very Positive",
                    },
                }
            return {
                "success": 1,
                "query_summary": {
                    "total_positive": 3000,
                    "total_negative": 1000,
                    "review_score_desc": "Mostly Positive",
                },
            }
        raise AssertionError(url)

    monkeypatch.setattr("app.collectors.steam.get_json", _fake_get_json)
    monkeypatch.setattr("app.services.collect.SessionLocal", session_factory)

    summary = await run_collection(fake_redis, target_id)

    assert summary["status"] == "success"
    assert summary["items_new"] == 1
    assert any(job[0] == "translate_content" for job in fake_redis.jobs)

    # 3. 公开内容流可按 platform=steam 检索，身份为官方
    list_resp = await client.get("/api/contents", params={"community": "wuwa", "platform": "steam"})
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["identity"] == "official"
    assert items[0]["title"] == "Version 2.4 Livestream Recap"

    # 4. Steam 遥测脉冲接口返回最新在线、24h 峰值、全区/简中好评率与中文评级
    pulse_resp = await client.get("/api/steam/pulse", params={"community": "wuwa", "hours": 168})
    assert pulse_resp.status_code == 200
    pulse = pulse_resp.json()
    assert len(pulse["apps"]) == 1
    app_data = pulse["apps"][0]
    assert app_data["app_id"] == 3513350
    assert app_data["name"] == "鸣潮"
    assert app_data["player_count"] == 31200
    assert app_data["peak_24h"] == 31200
    assert app_data["global_rate"] == 90.0
    assert app_data["global_score_label"] == "特别好评"
    assert app_data["cn_rate"] == 75.0
    assert app_data["price_formatted"] == "免费开玩"
    assert app_data["store_url"] == "https://store.steampowered.com/app/3513350/"
    assert len(app_data["series"]) == 1

    # 5. 未接入 Steam 的分区返回空数组
    empty_resp = await client.get("/api/steam/pulse", params={"community": "blhx"})
    assert empty_resp.status_code == 200
    assert empty_resp.json()["apps"] == []

    # 6. 重解析与 AI 翻译均支持 steam 平台
    async with session_factory() as s:
        reparsed = await reparse_contents(s, "steam")
        assert reparsed["checked"] == 1
        assert reparsed["failed"] == 0
        raw = await s.scalar(select(RawContent).where(RawContent.platform == "steam"))
        assert raw is not None
        monkeypatch.setattr(
            "app.services.ai.get_ai_config",
            AsyncMock(return_value={"base_url": "https://example.invalid/v1", "api_key": "k", "model": "fake-model", "enabled": True}),
        )
        monkeypatch.setattr(
            "app.services.ai.chat",
            AsyncMock(return_value=("2.4 版本前瞻汇总", {"prompt_tokens": 10, "completion_tokens": 8})),
        )
        tr_res = await translate_content(s, raw.id)
        assert tr_res["status"] == "ok"
        tr = await s.get(ContentTranslation, raw.id)
        assert tr is not None
        assert tr.text == "2.4 版本前瞻汇总"


@pytest.mark.asyncio
async def test_steam_snapshot_dedup_and_retention_thinning(session):
    now = datetime(2026, 6, 15, 12, 0, 0)
    # 5 分钟内重复保存会被去重
    first = await save_steam_snapshot(
        session,
        "wuwa",
        {"app_id": 3513350, "name": "鸣潮", "player_count": 20000, "global_score_desc": "No user reviews"},
        now=now,
    )
    dup = await save_steam_snapshot(
        session,
        "wuwa",
        {"app_id": 3513350, "name": "鸣潮", "player_count": 21000},
        now=now + timedelta(minutes=3),
    )
    assert first is not None and dup is not None
    assert dup.id == first.id

    # 30 天之前的同一天多条快照会被抽稀到 1 条，30 天内的全部保留
    old_day = datetime(2026, 5, 6, 0, 0, 0)
    for hour in (1, 6, 12):
        session.add(
            SteamAppSnapshot(
                community="wuwa",
                app_id=3513350,
                name="鸣潮",
                player_count=10000 + hour,
                captured_at=old_day + timedelta(hours=hour),
            )
        )
    recent_day = now - timedelta(days=5)
    for hour in (1, 6):
        session.add(
            SteamAppSnapshot(
                community="wuwa",
                app_id=3513350,
                name="鸣潮",
                player_count=15000 + hour,
                captured_at=recent_day + timedelta(hours=hour),
            )
        )
    await session.commit()

    removed = await thin_steam_snapshots(session, now=now)
    assert removed == 2

    remaining = list(await session.scalars(select(SteamAppSnapshot).order_by(SteamAppSnapshot.captured_at.asc())))
    assert len(remaining) == 4  # 1 old + 2 recent + 1 current

    # run_retention 也包含 steam_snapshots_removed 字段
    stats = await run_retention(session, now=now)
    assert "steam_snapshots_removed" in stats
    assert stats["steam_snapshots_removed"] == 0

    # No user reviews -> 暂无评测（按固定时间窗口读取）
    from app.services.steam import _score_label

    assert _score_label("No user reviews") == "暂无评测"
    assert _score_label("5 user reviews") == "评测较少"
    assert _score_label("Overwhelmingly Positive") == "好评如潮"
