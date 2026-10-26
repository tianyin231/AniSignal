"""新游戏官网适配、身份、防串区以及数据种子的回归。"""

import importlib.util
import json
from datetime import timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.collectors import game_official as collector_module
from app.collectors.base import CollectorError
from app.collectors.game_official import GameOfficialCollector
from app.communities import crosstalk_names
from app.models import CollectTarget
from app.models.enums import Identity
from app.parsers.bilibili import parse_video
from app.parsers.game_official import parse_notice
from app.parsers.youtube import parse_rss_item
from app.services.ai import _summary_supported
from app.services.collect import youtube_title_communities


@pytest.fixture(autouse=True)
def clear_cache():
    collector_module._DETAIL_CACHE.clear()
    yield
    collector_module._DETAIL_CACHE.clear()


def mock_http(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(collector_module.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))


async def test_genshin_official_body_images_and_publish_time(monkeypatch):
    now = utcnow().replace(microsecond=0) - timedelta(hours=2)
    publish = (now + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    row = {"iInfoId": 42, "sTitle": "原神版本公告", "sContent": '<p>更新说明</p><img src="https://example.com/cover.png">',
           "sExt": json.dumps({"image": [{"url": "https://example.com/cover.png"}]}),
           "dtStartTime": publish, "dtCreateTime": "2020-01-01 00:00:00", "dtEndTime": "2036-01-01 00:00:00"}

    def respond(request):
        assert request.url.params["iChanId"] == "719"
        return httpx.Response(200, json={"retcode": 0, "data": {"list": [row]}})

    mock_http(monkeypatch, respond)
    result = await GameOfficialCollector().fetch("notices", "genshin", 5)
    item = parse_notice(result.items[0])
    assert item.external_id == "genshin:42" and item.author.identity == Identity.OFFICIAL
    assert item.published_at == now.replace(tzinfo=timezone.utc)
    assert item.media == ["https://example.com/cover.png"]
    assert item.url == "https://ys.mihoyo.com/main/news/detail/42"
    assert "更新说明" in item.text


async def test_endfield_reads_full_detail_and_caches_it(monkeypatch):
    calls = []
    stamp = int((utcnow()-timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    row = {"cid": "6094", "title": "终末地版本公告", "displayTime": stamp, "brief": "列表摘要", "cover": "https://example.com/endfield.png"}

    def respond(request):
        calls.append(request)
        assert request.url.params["code"] == "endfield_web"
        return httpx.Response(200, json={"code": 0, "data": {"list": [row]} if request.url.path.endswith("/bulletin") else {**row, "data": "<p>完整维护说明与补偿。</p>"}})

    mock_http(monkeypatch, respond)
    first = await GameOfficialCollector().fetch("notices", "endfield", 5)
    again = await GameOfficialCollector().fetch("notices", "endfield", 5)
    item = parse_notice(first.items[0])
    assert "完整维护说明" in item.text and item.author.identity == Identity.OFFICIAL
    assert item.external_id == "endfield:6094" and item.url.endswith("/news/6094")
    assert again.items == first.items
    assert len(calls) == 3  # 两次列表，一次详情。


async def test_unsupported_provider_never_fetches(monkeypatch):
    def no_network(request):
        raise AssertionError("无效提供方不应请求外网")

    mock_http(monkeypatch, no_network)
    with pytest.raises(CollectorError):
        await GameOfficialCollector().fetch("notices", "https://example.com", 1)


def test_official_identities_and_endfield_placeholder_is_not_official():
    for mid in (401742377, 1265652806):
        assert parse_video({"bvid": "BVpublic", "owner_mid": mid}).author.identity == Identity.OFFICIAL
    for cid in ("UCiS882YPwZt1NfaM0gR0D9Q", "UCowPaVRBzg8CE6K4CB6LJfw"):
        assert parse_rss_item({"id": "public", "channel_id": cid}).author.identity == Identity.OFFICIAL
    assert parse_rss_item({"id": "public", "channel_id": "UCnhy93oVU2IRZmcpWm_RifQ"}).author.identity != Identity.OFFICIAL


def test_game_routing_and_summary_do_not_confuse_endfield_with_arknights():
    assert youtube_title_communities("Arknights: Endfield Version Preview") == {"endfield"}
    assert youtube_title_communities("明日方舟新活动") == {"arknights"}
    assert youtube_title_communities("Genshin Impact Version Preview") == {"genshin"}
    assert "原神" not in crosstalk_names("genshin") and "原神" in crosstalk_names("wuwa")
    assert _summary_supported("原神发布了新活动", "Genshin Impact new event", "genshin")
    assert _summary_supported("明日方舟：终末地发布新活动", "Arknights: Endfield new event", "endfield")
    assert not _summary_supported("明日方舟本体发布新活动", "Arknights: Endfield new event", "endfield")


async def test_official_target_cannot_be_assigned_to_another_game(client, admin_headers):
    body = {"platform": "game_official", "target_type": "notices", "target_value": "genshin", "community": "endfield"}
    assert (await client.post("/api/admin/collect-targets", headers=admin_headers, json=body)).status_code == 422
    body["community"] = "genshin"
    response = await client.post("/api/admin/collect-targets", headers=admin_headers, json=body)
    assert response.status_code == 201
    assert (await client.put(f"/api/admin/collect-targets/{response.json()['id']}", headers=admin_headers, json={"community": "endfield"})).status_code == 422


async def test_seed_is_idempotent_and_preserves_existing_frequency(engine):
    path = Path("alembic/versions/0047_genshin_endfield.py")
    spec = importlib.util.spec_from_file_location("seed_new_games", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def upgrade(conn):
        from unittest.mock import patch
        with patch.object(migration.op, "get_bind", return_value=conn):
            migration.upgrade()

    async with engine.begin() as conn:
        await conn.run_sync(upgrade)
        await conn.execute(CollectTarget.__table__.update().where(CollectTarget.target_value == "401742377").values(interval_minutes=75))
        await conn.run_sync(upgrade)
        rows = (await conn.execute(select(CollectTarget.__table__))).mappings().all()
    assert len(rows) == 11
    assert next(row for row in rows if row["target_value"] == "401742377")["interval_minutes"] == 75
    for community in ("genshin", "endfield"):
        assert {row["platform"] for row in rows if row["community"] == community} >= {"bilibili", "game_official", "youtube"}
