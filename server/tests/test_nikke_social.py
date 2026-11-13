"""NIKKE 国际服社交官号：身份、真实来源平台、X 冷却及独立定制数据。"""

import importlib.util
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from app.clock import utcnow
from app.collectors.base import CollectorDeferred
from app.custom_presets import CUSTOM_PRESETS, custom_preset
from app.models import CustomFeed, CustomFeedItem, RawContent
from app.parsers.x import parse_post
from app.parsers.youtube import parse_rss_item
from app.schemas.content import FetchResult
from app.services import custom_feeds
from app.services.custom_policy import shared_source
from app.services.custom_policy import canonical_target


YT = "UCmmgZwsqQRD6PKeo87uq1bA"


def rows(platform):
    recent=(utcnow()-timedelta(hours=1)).replace(tzinfo=timezone.utc).isoformat()
    old=(utcnow()-timedelta(days=1)).replace(tzinfo=timezone.utc).isoformat()
    if platform == "x":
        return [{"id":str(i),"author":"NIKKE_en","text":label,"published_at":stamp,"url":f"https://x.com/NIKKE_en/status/{i}"}
                for i,label,stamp in ((111,"Pinned old event",old),(222,"Latest official event",recent))]
    return [{"id":"yt-public","channel_id":YT,"title":"NIKKE official video","published":recent,
             "author":"GODDESS OF VICTORY: NIKKE","link":"https://www.youtube.com/watch?v=yt-public"}]


def test_only_exact_verified_accounts_are_presets_and_official():
    assert canonical_target("channel_rss",YT)==YT
    assert custom_preset("youtube","channel_rss",YT)["source_url"].endswith("@NIKKEEN")
    assert not custom_preset("youtube","channel_rss","UC-unverified")
    assert parse_rss_item(rows("youtube")[0]).author.identity=="official"
    assert parse_post(rows("x")[0]).author.identity=="official"
    assert parse_post({**rows("x")[0],"author":"nikke_fan"}).author.identity!="official"


@pytest.mark.parametrize("platform,preset_key",[("youtube","nikke_youtube"),("x","nikke_x")])
async def test_social_pipeline_keeps_platform_time_and_shared_rate_limit(session,client,monkeypatch,platform,preset_key):
    preset=CUSTOM_PRESETS[preset_key]
    source=await shared_source(session,platform,preset["kind"],preset["target_value"])
    feed=CustomFeed(source_id=source.id,name=preset["name"],platform=platform,kind=preset["kind"],target_value=preset["target_value"],is_public=True)
    session.add(feed);await session.commit()
    instances=[];slots=[];redis=SimpleNamespace()
    class Collector:
        async def fetch(self,*args):
            instances.append(self)
            return FetchResult(platform=platform,fetch_kind="x_posts" if platform=="x" else "youtube_rss",items=rows(platform))
    @asynccontextmanager
    async def slot(redis,platform,*args):
        slots.append(platform);yield
    monkeypatch.setattr(custom_feeds,"XCollector" if platform=="x" else "YoutubeApiCollector",Collector)
    monkeypatch.setattr(custom_feeds,"platform_slot",slot)
    stats=await custom_feeds.sweep_custom_feeds(session,redis)
    assert stats["sources"]==1 and stats["failed"]==0 and stats["pushed"]==0
    assert (instances[0].redis is redis and slots==[]) if platform=="x" else slots==["youtube"]
    assert await session.scalar(select(func.count(RawContent.id)))==0
    detail=(await client.get(f"/api/custom/feeds/{feed.id}")).json()
    assert detail["target_label"]==preset["target_label"] and detail["source_url"]==preset["source_url"]
    assert {item["platform"] for item in detail["items"]}=={platform}
    if platform=="x": assert detail["items"][0]["title"]=="Latest official event"
    await session.refresh(source); source.next_run_at=utcnow()+timedelta(hours=2)
    feed.next_run_at=None;await session.commit()
    await custom_feeds.sweep_custom_feeds(session,redis)
    assert len(instances)==1  # 个人分发再次到期仍复用共享缓存。


async def test_x_cooldown_defers_without_shortening_configured_interval(session,monkeypatch):
    preset=CUSTOM_PRESETS["nikke_x"]
    source=await shared_source(session,"x",preset["kind"],preset["target_value"])
    feed=CustomFeed(source_id=source.id,name=preset["name"],platform="x",kind=preset["kind"],target_value=preset["target_value"],interval_minutes=120)
    session.add(feed);await session.commit()
    class Collector:
        async def fetch(self,*args):raise CollectorDeferred("X 平台处于访问限制冷却期",retry_at=utcnow()+timedelta(hours=1))
    monkeypatch.setattr(custom_feeds,"XCollector",Collector)
    stats=await custom_feeds.sweep_custom_feeds(session,SimpleNamespace())
    await session.refresh(source)
    assert stats["deferred"]==1 and stats["failed"]==0 and source.consecutive_failures==0
    assert source.next_run_at>utcnow()+timedelta(minutes=119)
    assert source.lease_until is None


async def test_unapproved_social_account_is_not_collected(session,monkeypatch):
    source=await shared_source(session,"x","account_dynamics","random_account")
    session.add(CustomFeed(source_id=source.id,name="未核验",platform="x",kind="account_dynamics",target_value="random_account"))
    await session.commit()
    stats=await custom_feeds.sweep_custom_feeds(session,SimpleNamespace())
    assert stats["sources"]==0


async def test_social_seed_preserves_existing_official_target_and_is_idempotent(session):
    from app.models import CollectTarget
    from app.seeds import import_preset_targets

    existing = CollectTarget(community="nikke", platform="game_official", target_type="notices", target_value="nikke", interval_minutes=75)
    session.add(existing)
    await session.commit()

    await import_preset_targets(session, ["nikke"])
    await import_preset_targets(session, ["nikke"])
    await session.refresh(existing)
    assert existing.interval_minutes == 75
    targets = list(await session.scalars(select(CollectTarget).where(CollectTarget.community == "nikke")))
    assert len(targets) == 3 and {t.platform for t in targets} == {"game_official", "youtube", "x"}
    x_target = next(t for t in targets if t.platform == "x")
    assert x_target.interval_minutes == 120 and not x_target.enabled
