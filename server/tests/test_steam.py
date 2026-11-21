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
            if params.get("cc") == "cn":
                # 模拟锁国区游戏（如鸣潮 / 绝区零 / 终末地）在 cc=cn 下返回 success=False
                return {"3513350": {"success": False}}
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


@pytest.mark.asyncio
async def test_steam_aggregate_pulse_sorts_by_ccu_and_respects_hidden_blocks(client, session):
    now = utcnow()
    session.add_all([
        CollectTarget(community="wuwa", platform="steam", target_type="notices", target_value="3513350", display_name="鸣潮", enabled=True),
        CollectTarget(community="zzz", platform="steam", target_type="notices", target_value="4162040", display_name="绝区零", enabled=True),
        SteamAppSnapshot(community="wuwa", app_id=3513350, name="Wuthering Waves", player_count=8200, global_positive=85, global_negative=15, global_score_desc="Very Positive", captured_at=now),
        SteamAppSnapshot(community="zzz", app_id=4162040, name="绝区零", player_count=12500, global_positive=90, global_negative=10, global_score_desc="Very Positive", captured_at=now),
        # 定制监控快照不得泄漏到公开聚合首页
        SteamAppSnapshot(community="custom", app_id=2358720, name="黑神话：悟空", player_count=99999, captured_at=now),
    ])
    await session.commit()

    resp = await client.get("/api/steam/pulse", params={"community": "all", "hours": 168})
    assert resp.status_code == 200
    apps = resp.json()["apps"]
    assert [a["community"] for a in apps] == ["zzz", "wuwa"]
    assert all(a["app_id"] != 2358720 for a in apps)

    # 屏蔽分区 wuwa 后只保留 zzz（需带有效登录会话 Cookie）
    client.cookies.set("anisignal_session", "dummy-session")
    client.cookies.set("anisignal_hidden", "wuwa")
    resp_hidden_comm = await client.get("/api/steam/pulse", params={"community": "all"})
    assert [a["community"] for a in resp_hidden_comm.json()["apps"]] == ["zzz"]

    # 屏蔽来源平台 game:steam 后全空
    client.cookies.set("anisignal_hidden", "game:steam")
    resp_hidden_plat = await client.get("/api/steam/pulse", params={"community": "all"})
    assert resp_hidden_plat.json()["apps"] == []
    client.cookies.clear()


@pytest.mark.asyncio
async def test_steam_custom_monitor_end_to_end(client, admin_headers, session, monkeypatch):
    from contextlib import asynccontextmanager
    from app.models import CustomFeed, User
    from app.services.auth import create_session, hash_password
    from app.services.custom_feeds import sweep_custom_feeds
    from app.services.settings import set_setting
    import app.services.custom_feeds as cf

    await set_setting(session, "custom_feed_policy", {"open_beta": True, "min_interval_minutes": 30})
    user = User(email="steam_user@example.com", password_hash=hash_password("pass123456"), display_name="Steam玩家", is_verified=True, status="active")
    session.add(user)
    await session.commit()
    token = await create_session(session, user)
    client.cookies.set("anisignal_session", token)

    # 非法链接拦截
    bad = await client.post("/api/account/custom-feeds", json={
        "name": "无效Steam",
        "kind": "steam_app",
        "platform": "steam",
        "target_value": "https://example.com/app/2358720",
        "interval_minutes": 60,
    })
    assert bad.status_code == 400

    # 支持粘贴商店链接，自动规范化为纯数字 AppID
    create_resp = await client.post("/api/account/custom-feeds", json={
        "name": "黑神话：悟空 Steam",
        "kind": "steam_app",
        "platform": "steam",
        "target_value": "https://store.steampowered.com/app/2358720/Black_Myth_Wukong/",
        "interval_minutes": 60,
        "is_public": True,
    })
    assert create_resp.status_code == 201, create_resp.text
    feed = create_resp.json()
    feed_row = await session.get(CustomFeed, feed["id"])
    assert feed_row is not None
    assert feed_row.target_value == "2358720"
    assert feed_row.platform == "steam"
    assert feed_row.kind == "steam_app"

    async def _fake_get_json(_client, url, *, params=None, **_kwargs):
        params = dict(params or {})
        if url == STEAM_NEWS_URL:
            return {
                "appnews": {
                    "appid": 2358720,
                    "newsitems": [
                        {
                            "gid": "888001",
                            "title": "Black Myth: Wukong Patch 1.0.20",
                            "contents": "Performance improvements and bug fixes.",
                            "date": int(utcnow().timestamp()) - 120,
                            "feedname": "steam_community_announcements",
                        }
                    ],
                }
            }
        if url == STEAM_APPDETAILS_URL:
            return {
                "2358720": {
                    "success": True,
                    "data": {
                        "name": "黑神话：悟空",
                        "is_free": False,
                        "price_overview": {"final_formatted": "¥ 268.00", "discount_percent": 20},
                    },
                }
            }
        if url == STEAM_ONLINE_URL:
            return {"response": {"player_count": 45600, "result": 1}}
        if "/ajaxappreviews/" in url:
            if params["language"] == "all":
                return {"success": 1, "query_summary": {"total_positive": 96000, "total_negative": 4000, "review_score_desc": "Overwhelmingly Positive"}}
            return {"success": 1, "query_summary": {"total_positive": 85000, "total_negative": 3000, "review_score_desc": "Overwhelmingly Positive"}}
        raise AssertionError(url)

    @asynccontextmanager
    async def _slot(_redis, _platform, _seconds):
        yield

    monkeypatch.setattr("app.collectors.steam.get_json", _fake_get_json)
    monkeypatch.setattr(cf, "platform_slot", _slot)

    stats = await sweep_custom_feeds(session, None)
    assert stats["new_items"] == 1
    assert stats["failed"] == 0

    # 定制监控详情页返回官方公告 + steam_pulse 实时数据 + 商店链接
    detail_resp = await client.get(f"/api/custom/feeds/{feed['id']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["source_url"] == "https://store.steampowered.com/app/2358720/"
    assert detail["total"] == 1
    assert detail["items"][0]["title"] == "Black Myth: Wukong Patch 1.0.20"
    pulse = detail["steam_pulse"]
    assert pulse is not None
    assert pulse["app_id"] == 2358720
    assert pulse["name"] == "黑神话：悟空"
    assert pulse["player_count"] == 45600
    assert pulse["global_rate"] == 96.0
    assert pulse["global_score_label"] == "好评如潮"
    assert pulse["price_formatted"] == "¥ 268.00"
    assert pulse["discount_percent"] == 20

    # 定制监控条目不污染主内容库与主站聚合 Steam 脉冲
    assert await session.scalar(select(RawContent).where(RawContent.platform == "steam")) is None
    assert (await client.get("/api/steam/pulse", params={"community": "all"})).json()["apps"] == []

    # 管理端按 kind=steam_app 筛选可用
    admin_list = await client.get("/api/admin/custom-feeds", headers=admin_headers, params={"kind": "steam_app"})
    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == 1
    assert await session.get(CustomFeed, feed["id"]) is not None
