import importlib.util
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from app.api.bot import TargetBody
from app.clock import utcnow
from app.collectors.game_official import GameOfficialCollector
from app.collectors.nikke import parse_notice
from app.custom_presets import CUSTOM_PRESETS, promote_subscriptions
from app.models import BotTarget, CollectTarget, CustomFeed, CustomFeedItem, CustomSourceItem, RawContent
from app.schemas.content import FetchResult
from app.services.custom_policy import shared_source
from app.services.ingest import ingest_items, reparse_contents
from app.services.promote_nikke import promote_nikke
from app.services.events import run_aggregation
from app.services.views import events_to_out
from app.models import Event
from tests.test_nikke_custom import article


async def test_nikke_official_collector_keeps_proxy_and_uses_existing_parser(monkeypatch, session):
    async def fetch(self, kind, value, limit):
        assert self.proxy_url == "http://proxy.example:8080"
        assert (kind, value, limit) == ("notices", "nikke_global", 5)
        return FetchResult(platform="game_official", fetch_kind="nikke_notices", items=[article()])
    monkeypatch.setattr("app.collectors.game_official.NikkeCollector.fetch", fetch)
    collector = GameOfficialCollector()
    collector.proxy_url = "http://proxy.example:8080"
    result = await collector.fetch("notices", "nikke", 5)
    stats = await ingest_items(session, result.fetch_kind, result.items, community="nikke")
    assert stats.items_new == 1 and not stats.errors
    assert (await reparse_contents(session, "game_official"))["failed"] == 0
    await run_aggregation(session)
    events = list(await session.scalars(select(Event).where(Event.community == "nikke")))
    assert events
    output = await events_to_out(session, events, include_detail=True)
    assert all(evidence.region == "global" for event in output for evidence in event.sources)


def test_legacy_subscriptions_become_exact_official_channels_without_changing_other_settings():
    config = {"communities": ["wuwa"], "community": "wuwa", "custom_feeds": ["nikke_x"],
              "channels": [], "scope_mode": "legacy", "quiet_start": 23,
              "daily_sources": ["preset:nikke_x"], "updates_sources": ["wuwa:official:bilibili"]}
    converted = promote_subscriptions(config)
    assert config["custom_feeds"] == ["nikke_x"]
    assert converted["custom_feeds"] == [] and converted["scope_mode"] == "channels"
    assert converted["quiet_start"] == 23 and converted["communities"] == ["wuwa", "nikke"]
    assert "wuwa:community:youtube" in converted["channels"]
    assert "nikke:official:x" in converted["channels"] and "nikke:official:youtube" not in converted["channels"]
    assert converted["daily_sources"] == ["nikke:official:x"]
    assert converted["updates_sources"] == config["updates_sources"]
    assert promote_subscriptions(converted) == converted
    body = TargetBody(name="旧插件配置", umo="mock:GroupMessage:old", **config)
    assert body.channels == converted["channels"] and body.custom_feeds == []


async def test_seed_and_archive_preserve_history_frequency_subscription_and_old_links(engine, session, client):
    preset = CUSTOM_PRESETS["nikke_global"]
    source = await shared_source(session, preset["platform"], preset["kind"], preset["target_value"])
    collected = utcnow() - timedelta(days=2)
    feed = CustomFeed(source_id=source.id, name=preset["name"], platform=source.platform, kind=source.kind,
                      target_value=source.target_value, interval_minutes=75, is_public=True)
    session.add(feed)
    await session.flush()
    normalized = parse_notice(article("archive", age=3)).model_dump(mode="json")
    cache = CustomSourceItem(source_id=source.id, external_id=normalized["external_id"],
                             normalized=normalized, collected_at=collected)
    session.add(cache)
    session.add(CustomFeedItem(feed_id=feed.id, platform=source.platform, external_id=normalized["external_id"],
                               title=normalized["title"], collected_at=collected))
    target = BotTarget(name="原订阅", umo="mock:GroupMessage:archive", config={
        "communities": ["wuwa"], "custom_feeds": ["nikke_global"]}, last_scan_at=utcnow())
    session.add(target)
    await session.commit()
    path = Path("alembic/versions/0056_nikke_infinitynikki.py")
    spec = importlib.util.spec_from_file_location("promote_games", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    def apply(conn):
        with patch.object(migration, "op", Operations(MigrationContext.configure(conn))):
            migration.upgrade()
            migration.upgrade()
    async with engine.begin() as conn:
        await conn.run_sync(apply)
    await session.refresh(feed)
    assert not feed.enabled and feed.is_public
    targets = list(await session.scalars(select(CollectTarget)))
    assert len(targets) == 5
    assert next(t for t in targets if t.community == "nikke" and t.platform == "game_official").interval_minutes == 75
    result = await promote_nikke(session)
    assert result == {"imported": 1, "already_present": 0, "subscriptions_migrated": 1}
    raw = await session.scalar(select(RawContent))
    assert raw.community == "nikke" and raw.collected_at == collected
    assert raw.raw_json["normalized"] == normalized and raw.published_at.isoformat() in normalized["published_at"]
    assert await session.scalar(select(func.count()).select_from(CustomFeedItem)) == 1
    assert await session.scalar(select(func.count()).select_from(CustomSourceItem)) == 1
    assert (await promote_nikke(session))["imported"] == 0
    assert (await reparse_contents(session, "game_official"))["failed"] == 0
    assert target.config["custom_feeds"] == [] and "nikke:official:web" in target.config["channels"]
    directory = (await client.get("/api/custom/feeds")).json()
    assert directory["total"] == 0
    assert (await client.get(f"/api/custom/feeds/{feed.id}")).json()["community"] == "nikke"
    assert (await client.get("/api/contents", params={"community": "nikke", "include_chat": True})).json()["total"] == 1
    assert (await client.post("/api/admin/collect-targets", headers={"X-Admin-Token": "test-admin-token"}, json={
        "platform": "game_official", "target_type": "notices", "target_value": "nikke", "community": "wuwa"})).status_code == 422
