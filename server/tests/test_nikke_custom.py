"""NIKKE 国际服官方来源、站点监控权限和迁移回归。"""

import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from app.clock import utcnow
from app.collectors import nikke
from app.collectors.base import CollectorError
from app.models import CustomFeed, CustomFeedItem, NotificationDelivery, RawContent, User
from app.schemas.content import FetchResult
from app.services import custom_feeds
from app.services.custom_policy import capacity, shared_source, user_quota


def article(cid="public", age=1):
    return {"content_id": cid, "title": "Update &amp; Maintenance", "content_part": "Official update notes",
            "pub_timestamp": str(int((utcnow()-timedelta(days=age)).replace(tzinfo=timezone.utc).timestamp())),
            "view_num": 120, "pic_urls": ["https://example.com/official.jpg"]}


def mock_http(monkeypatch, respond):
    real = httpx.AsyncClient
    monkeypatch.setattr(nikke.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(nikke.asyncio, "sleep", AsyncMock())


async def test_official_columns_use_english_global_deduplicate_and_order_by_original_time(monkeypatch):
    calls=[]
    def respond(request):
        data=json.loads(request.content); calls.append(data)
        assert request.method=="POST" and request.url==nikke.API
        assert data["language"]==["en"] and request.headers["X-GameId"]=="16"
        rows={496:[article("old",5),article("new",1)],892:[article("new",1),article("latest",0)],494:[article("old",5)]}
        return httpx.Response(200,json={"code":0,"data":{"info_content":rows[data["secondary_label_id"]]}})
    mock_http(monkeypatch,respond)
    result=await nikke.NikkeCollector().fetch("notices","nikke_global",3)
    assert [r["content_id"] for r in result.items]==["latest","new","old"]
    assert {r["secondary_label_id"] for r in calls}=={496,892,494}
    parsed=nikke.parse_notice(result.items[0])
    assert parsed.platform=="game_official" and parsed.content_type=="notice"
    assert parsed.author.identity=="official" and parsed.title=="Update & Maintenance"
    assert parsed.published_at.timestamp()==int(result.items[0]["pub_timestamp"])
    assert parsed.url=="https://nikke-en.com/newsdetail.html?content_id=latest&father_content_id=latest"


async def test_invalid_target_cannot_fetch_arbitrary_site(monkeypatch):
    http=AsyncMock();monkeypatch.setattr(nikke.httpx,"AsyncClient",http)
    with pytest.raises(CollectorError):
        await nikke.NikkeCollector().fetch("notices","https://example.com",1)
    http.assert_not_called()


@pytest.mark.parametrize("payload",[{"code":1},{"code":0,"data":{"info_content":[]}}])
async def test_api_error_and_empty_result_are_visible_failures(monkeypatch,payload):
    mock_http(monkeypatch,lambda request:httpx.Response(200,json=payload))
    with pytest.raises(CollectorError): await nikke.NikkeCollector().fetch("notices","nikke_global",2)


async def test_site_feed_collects_publicly_without_user_or_personal_notifications(session,client,monkeypatch,admin_headers):
    source=await shared_source(session,"game_official","notices","nikke_global")
    feed=CustomFeed(source_id=source.id,name="NIKKE 国际服",kind="notices",platform="game_official",
                    target_value="nikke_global",is_public=True,push_enabled=True)
    session.add(feed);await session.commit()
    fetch=AsyncMock(return_value=FetchResult(platform="game_official",fetch_kind="nikke_notices",items=[article()]))
    class Collector:
        async def fetch(self,*args):return await fetch(*args)
    @asynccontextmanager
    async def slot(*args):yield
    monkeypatch.setattr(custom_feeds,"NikkeCollector",Collector)
    monkeypatch.setattr(custom_feeds,"platform_slot",slot)
    stats=await custom_feeds.sweep_custom_feeds(session,None)
    assert stats["sources"]==stats["new_items"]==stats["feeds"]==1 and stats["pushed"]==0
    assert await session.scalar(select(func.count(RawContent.id)))==0
    assert await session.scalar(select(func.count(NotificationDelivery.id)))==0
    assert (await client.get("/api/custom/feeds")).json()["total"]==1
    detail=(await client.get(f"/api/custom/feeds/{feed.id}")).json()
    assert detail["site_managed"] is True and detail["is_owner"] is False
    assert detail["target_label"]=="NIKKE 国际服官网（英文）" and detail["items"][0]["content_type"]=="notice"
    assert "站点维护" in detail["owner_name"]
    user=User(email="nikke-reader@example.com",password_hash="x",is_verified=True)
    session.add(user);await session.commit()
    assert (await user_quota(session,user.id))["used"]==0
    assert (await capacity(session))["active_sources"]==1
    from app.services.auth import create_session
    client.cookies.set("anisignal_session",await create_session(session,user))
    assert (await client.patch(f"/api/account/custom-feeds/{feed.id}",json={"name":"冒用"})).status_code==404
    assert (await client.delete(f"/api/account/custom-feeds/{feed.id}")).status_code==404
    admin=(await client.get("/api/admin/custom-feeds",headers=admin_headers,params={"kind":"notices"})).json()
    assert admin["total"]==1 and admin["items"][0]["user_id"] is None
    await client.patch(f"/api/admin/custom-feeds/{feed.id}",json={"status":"disabled"},headers=admin_headers)
    assert (await client.get("/api/custom/feeds")).json()["total"]==0
    assert (await client.get(f"/api/custom/feeds/{feed.id}")).status_code==404


async def test_migration_seeds_once_and_preserves_existing_private_monitor(engine,session):
    user=User(email="legacy-nikke@example.com",password_hash="x",is_verified=True)
    session.add(user);await session.flush()
    legacy=CustomFeed(user_id=user.id,name="已有私有监控",kind="uid",target_value="123",interval_minutes=5,push_enabled=True)
    session.add(legacy);await session.flush()
    old_item=CustomFeedItem(feed_id=legacy.id,platform="bilibili",external_id="history")
    session.add(old_item);await session.commit()
    path=Path("alembic/versions/0050_nikke_custom_feed.py")
    spec=importlib.util.spec_from_file_location("nikke_migration",path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    def apply(conn):
        with patch.object(migration,"op",Operations(MigrationContext.configure(conn))):
            migration._owner_nullable(False)  # 模拟 0049 的非空所有者。
            migration.upgrade()
            migration.upgrade()
    async with engine.begin() as conn: await conn.run_sync(apply)
    await session.refresh(legacy)
    assert legacy.interval_minutes==5 and legacy.push_enabled and not legacy.is_public
    assert await session.get(CustomFeedItem,old_item.id) is not None
    seeds=list(await session.scalars(select(CustomFeed).where(CustomFeed.user_id.is_(None))))
    assert len(seeds)==1 and seeds[0].source_id and seeds[0].is_public and not seeds[0].push_enabled
