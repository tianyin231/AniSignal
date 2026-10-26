"""共享目标、公测配额、预算、退避与迁移保留历史的回归。"""

import importlib.util
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.clock import utcnow
from app.collectors.base import CollectorDeferred, CollectorError
from app.models import CustomFeed, CustomFeedItem, CustomFeedSource, CustomCollectRun, RawContent, User
from app.schemas.content import NormalizedContent, NormalizedAuthor
from app.services import custom_feeds as cf
from app.services.custom_policy import capacity, claim_collection_budget, shared_source
from app.services.settings import set_setting


async def users(session):
    owners = [User(email=f"shared-{i}@example.com", password_hash="x", is_verified=True) for i in range(2)]
    session.add_all(owners); await session.flush()
    return owners


def content(external_id, title, view=50000):
    return NormalizedContent(platform="bilibili", content_type="video", external_id=external_id,
        title=title, text="公开内容", author=NormalizedAuthor(name="公开作者"), metrics={"view": view})


async def test_one_fetch_different_filters_visibility_and_intervals(session, monkeypatch, client):
    a,b = await users(session)
    source = await shared_source(session, "bilibili", "keyword", "共享目标")
    feeds = [CustomFeed(user_id=a.id, source_id=source.id, name="私人", kind="keyword", target_value="共享目标",
                        min_views=10000, filters={"exclude": ["剧透"]}, interval_minutes=30),
             CustomFeed(user_id=b.id, source_id=source.id, name="公开", kind="keyword", target_value="共享目标",
                        is_public=True, interval_minutes=60, filters={"include": ["剧情"]})]
    session.add_all(feeds); await session.commit()
    fetch = AsyncMock(return_value=[content("hot", "版本攻略"), content("spoiler", "剧情剧透"), content("cold", "剧情讨论", 500)])
    monkeypatch.setattr(cf, "fetch_custom_items", fetch)
    first = await cf.sweep_custom_feeds(session, None)
    assert first["sources"] == 1 and first["feeds"] == 2 and first["new_items"] == 3
    assert fetch.await_count == 1
    assert await session.scalar(sa.select(sa.func.count(RawContent.id))) == 0
    rows = list(await session.scalars(sa.select(CustomFeedItem)))
    assert {r.external_id for r in rows if r.feed_id == feeds[0].id} == {"hot"}
    assert {r.external_id for r in rows if r.feed_id == feeds[1].id} == {"spoiler", "cold"}
    assert (await capacity(session)) == {"active_sources": 1, "estimated_tasks_per_hour": 2.0}
    assert (feeds[1].next_run_at-feeds[1].last_collected_at).total_seconds() == 3600
    assert (await client.get(f"/api/custom/feeds/{feeds[0].id}")).status_code == 404
    assert (await client.get(f"/api/custom/feeds/{feeds[1].id}")).json()["total"] == 2
    # 仅改个人过滤后可从共享缓存补分发，不能再次访问 B站。
    feeds[0].filters = {}; feeds[0].next_run_at = None; await session.commit()
    again = await cf.sweep_custom_feeds(session, None)
    assert again["sources"] == 0 and again["new_items"] == 1 and fetch.await_count == 1


async def test_failure_backoff_cookie_redaction_and_cooldown_do_not_starve_others(session, monkeypatch):
    a,_ = await users(session)
    feeds=[CustomFeed(user_id=a.id, name=t, kind="keyword", target_value=t) for t in ("失败目标", "正常目标")]
    session.add_all(feeds); await session.commit()
    async def fetch(source, *args):
        if source.target_value == "失败目标": raise CollectorError("test SESSDATA=hidden-secret; HTTP 412")
        return [content("good", "正常内容")]
    mocked=AsyncMock(side_effect=fetch); monkeypatch.setattr(cf, "fetch_custom_items", mocked)
    stats=await cf.sweep_custom_feeds(session,None)
    assert stats["failed"]==1 and stats["new_items"]==1
    source=await session.scalar(sa.select(CustomFeedSource).where(CustomFeedSource.target_value=="失败目标"))
    assert source.consecutive_failures==1 and "hidden-secret" not in source.last_error
    assert source.next_run_at > utcnow()+timedelta(minutes=59)
    stats=await cf.sweep_custom_feeds(session,None,limit=1)
    assert stats["failed"]==0 and mocked.await_count==2
    assert (await session.scalar(sa.select(CustomCollectRun).where(CustomCollectRun.source_id==source.id))).status=="failed"


async def test_source_lease_blocks_duplicate_collectors(session, monkeypatch):
    a,_=await users(session)
    source=await shared_source(session,"bilibili","uid","000123")
    assert source.target_value=="123"
    source.lease_until=utcnow()+timedelta(minutes=10)
    session.add(CustomFeed(user_id=a.id,source_id=source.id,name="动态",kind="uid",target_value="123"))
    await session.commit()
    fetch=AsyncMock();monkeypatch.setattr(cf,"fetch_custom_items",fetch)
    await cf.sweep_custom_feeds(session,None)
    fetch.assert_not_awaited()


async def test_public_and_custom_share_budget_and_resume_next_hour(session):
    await set_setting(session,"custom_feed_policy",{"platform_hourly_budget":2,"custom_hourly_budget":1})
    await claim_collection_budget(session,"bilibili")
    await claim_collection_budget(session,"bilibili",custom=True)
    with pytest.raises(CollectorDeferred) as error:
        await claim_collection_budget(session,"bilibili")
    assert error.value.retry_at.minute==0 and error.value.retry_at>utcnow()
    with pytest.raises(CollectorDeferred):
        await claim_collection_budget(session,"bilibili",custom=True)


async def test_public_budget_defers_before_network_without_marking_failure(session,session_factory,monkeypatch):
    from app.models import CollectTarget
    from app.services import collect as collect_service
    from tests.test_collect_service import _FakeRedis
    await set_setting(session,"custom_feed_policy",{"platform_hourly_budget":1,"custom_hourly_budget":1})
    await claim_collection_budget(session,"bilibili",custom=True)
    target=CollectTarget(platform="bilibili",target_type="keyword_search",target_value="预算测试")
    session.add(target);await session.commit()
    monkeypatch.setattr(collect_service,"SessionLocal",session_factory)
    fetch=AsyncMock()
    class Collector:
        async def fetch(self,*args): return await fetch(*args)
    monkeypatch.setitem(collect_service._COLLECTORS,"bilibili",Collector)
    result=await collect_service.run_collection(_FakeRedis(),target.id)
    assert result["status"]=="skipped" and result["retry_at"]>utcnow()
    await session.refresh(target)
    assert target.consecutive_failures==0
    fetch.assert_not_awaited()


async def test_uid_pipeline_uses_one_shared_dynamics_fetch(session,monkeypatch):
    from contextlib import asynccontextmanager
    from tests.test_parsers_bilibili import DYNAMIC_RAW
    a,b=await users(session)
    session.add_all([CustomFeed(user_id=u.id,name="公开动态",kind="uid",target_value="1955897084") for u in (a,b)])
    await session.commit()
    fetch=AsyncMock(return_value=[dict(DYNAMIC_RAW,text="公开版本动态")])
    class Collector:
        user_dynamics=fetch
    @asynccontextmanager
    async def slot(*args): yield
    monkeypatch.setattr(cf,"BilibiliCollector",Collector);monkeypatch.setattr(cf,"platform_slot",slot)
    result=await cf.sweep_custom_feeds(session,None)
    assert result["sources"]==1 and result["new_items"]==2 and fetch.await_count==1
    assert set(await session.scalars(sa.select(CustomFeedItem.content_type)))=={"dynamic"}


async def test_controlled_admission_capacity_and_legacy_frequency(client,session,admin_headers):
    a,_=await users(session);await session.commit()
    from app.services.auth import create_session
    client.cookies.set("anisignal_session",await create_session(session,a))
    body={"name":"目标","kind":"keyword","target_value":"关键词"}
    assert (await client.post("/api/account/custom-feeds",json=body)).status_code==403
    assert (await client.get("/api/account/custom-quota")).json()["admitted"] is False
    policy={"allowed_user_ids":[a.id],"max_feeds_per_user":1,"max_active_sources":1}
    assert (await client.put("/api/admin/custom-feeds/policy",headers=admin_headers,json=policy)).status_code==200
    response=await client.post("/api/account/custom-feeds",json=body);assert response.status_code==201
    assert (await client.post("/api/account/custom-feeds",json={**body,"target_value":"另一个"})).status_code==400
    feed=await session.get(CustomFeed,response.json()["id"]);feed.interval_minutes=5;await session.commit()
    endpoint=f"/api/account/custom-feeds/{feed.id}"
    assert (await client.patch(endpoint,json={"name":"保留旧周期","interval_minutes":5})).status_code==200
    assert (await client.patch(endpoint,json={"interval_minutes":4})).status_code==400
    assert (await client.patch(endpoint,json={"filters":{"exclude":["剧透"]},"is_public":True,"push_enabled":False})).status_code==200
    await session.refresh(feed)
    assert feed.interval_minutes==5 and feed.is_public and not feed.push_enabled


async def test_capacity_counts_targets_not_users(session):
    a,b=await users(session)
    session.add_all([CustomFeed(user_id=a.id,name=str(i),kind="keyword",target_value=str(i),interval_minutes=30) for i in range(500)])
    # 另一个用户订阅相同目标不会把 1000 任务/小时翻倍。
    session.add(CustomFeed(user_id=b.id,name="重复",kind="keyword",target_value="0",interval_minutes=60))
    await session.commit()
    assert (await capacity(session)) == {"active_sources":500,"estimated_tasks_per_hour":1000.0}


async def test_migration_preserves_history_and_deduplicates_sources():
    from sqlalchemy.ext.asyncio import create_async_engine
    engine=create_async_engine("sqlite+aiosqlite://")
    path=Path("alembic/versions/0048_custom_shared_sources.py")
    spec=importlib.util.spec_from_file_location("custom_shared_migration",path); migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    def apply(conn):
        conn.exec_driver_sql("CREATE TABLE custom_feeds (id INTEGER PRIMARY KEY,user_id INTEGER,platform VARCHAR(16),kind VARCHAR(16),target_value VARCHAR(128),interval_minutes INTEGER,is_public BOOLEAN,push_enabled BOOLEAN)")
        conn.exec_driver_sql("INSERT INTO custom_feeds VALUES (1,1,'bilibili','uid','00123',5,0,1),(2,2,'bilibili','uid','123',60,1,0)")
        conn.exec_driver_sql("CREATE TABLE custom_feed_items (id INTEGER PRIMARY KEY,feed_id INTEGER,external_id TEXT)")
        conn.exec_driver_sql("INSERT INTO custom_feed_items VALUES (99,1,'history')")
        with patch.object(migration,"op",Operations(MigrationContext.configure(conn))): migration.upgrade()
        rows=conn.exec_driver_sql("SELECT id,source_id,interval_minutes,is_public,push_enabled FROM custom_feeds ORDER BY id").all()
        assert rows[0][1]==rows[1][1] and rows[0][2:]==(5,0,1) and rows[1][2:]==(60,1,0)
        assert conn.exec_driver_sql("SELECT id FROM custom_feed_items").scalar()==99
    async with engine.begin() as conn: await conn.run_sync(apply)
    await engine.dispose()
