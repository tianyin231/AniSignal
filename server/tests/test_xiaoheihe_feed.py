from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.collectors.base import CollectorError
from app.collectors.rss import RssCollector, parse_feed
from app.config import get_settings
from app.models import CustomFeed, User
from app.schemas.content import FetchResult
from app.services.auth import create_session, hash_password
from app.services.custom_feeds import sweep_custom_feeds
from app.services.extract import extract_entities, sweep_extractions
from app.services.ingest import ingest_items, reparse_contents
from app.services.notifications import load_candidates
from app.services.settings import set_setting
from app.services.xiaoheihe import get_xiaoheihe_source


async def test_topic_feed_collects_refs_without_feeding_game_pipelines(client, session, admin_headers):
    for key, label in [("player-talk", "玩家社区"), ("guides", "攻略")]:
        response = await client.post(
            "/api/admin/communities",
            headers=admin_headers,
            json={"key": key, "label": label, "domain": "feed"},
        )
        assert response.status_code == 201
        assert response.json()["domain"] == "feed"
    config = (await client.get("/api/site-config")).json()
    topic = next(item for item in config["communities"] if item["key"] == "player-talk")
    assert topic["domain"] == "feed"
    assert "xiaoheihe" in topic["platforms"]

    share_url = "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=feed123&h_camp=link"
    original_url = "https://www.xiaoheihe.cn/app/bbs/link/feed123"
    post = {"community": "player-talk", "url": share_url, "title": "玩家的更新实测", "author": "盒友"}
    endpoint = "/api/admin/contents/xiaoheihe"
    response = await client.post(endpoint, headers=admin_headers, json=post)
    assert response.status_code == 200
    content_id = response.json()["id"]
    assert response.json()["created"]

    duplicate = await client.post(
        endpoint, headers=admin_headers, json={**post, "url": original_url, "author": "另一个昵称"}
    )
    assert duplicate.json() == {"id": content_id, "created": False}
    assert (await client.post(endpoint, headers=admin_headers, json={**post, "community": "guides"})).status_code == 409

    listing = await client.get("/api/contents", params={"community": "player-talk", "platform": "xiaoheihe"})
    assert listing.json()["total"] == 1
    item = listing.json()["items"][0]
    assert (item["title"], item["source_name"], item["identity"], item["url"]) == (
        post["title"], post["author"], "user", original_url,
    )
    assert item["preview_mode"] == "external_only"
    assert not item["text_excerpt"]
    b_station = {
        "bvid": "BV1topic1234", "title": "实用攻略", "owner_mid": 123, "owner_name": "说明作者",
    }
    stats = await ingest_items(session, "bilibili_search", [b_station], "guides", force_official=True)
    assert stats.items_new == 1
    guides = await client.get("/api/contents", params={"community": "guides"})
    assert [item["title"] for item in guides.json()["items"]] == [b_station["title"]]
    assert (await client.get("/api/contents", params={"community": "all"})).json()["total"] == 0
    assert await load_candidates(session, None, utcnow() - timedelta(days=1), utcnow()) == []
    assert (await sweep_extractions(session))["candidates"] == 0
    assert (await extract_entities(session, stats.touched_content_ids[0]))["reason"] == "topic_feed"
    assert await reparse_contents(session, "xiaoheihe") == {"checked": 1, "updated": 0, "failed": 0}


async def test_share_requires_admin_topic_and_official_post_url(client, admin_headers):
    await client.post(
        "/api/admin/communities", headers=admin_headers, json={"key": "topic", "label": "主题流", "domain": "feed"}
    )
    endpoint = "/api/admin/contents/xiaoheihe"
    post = {
        "community": "topic",
        "url": "https://www.xiaoheihe.cn/app/bbs/link/feed123",
        "title": "玩家讨论",
        "author": "盒友",
    }
    assert (await client.post(endpoint, json=post)).status_code == 401
    assert (await client.post(endpoint, headers=admin_headers, json={**post, "community": "wuwa"})).status_code == 400
    invalid = {**post, "url": "https://www.xiaoheihe.cn.example.com/app/bbs/link/feed123"}
    assert (await client.post(endpoint, headers=admin_headers, json=invalid)).status_code == 400


async def test_xiaoheihe_public_source_is_shared_and_keeps_posts_on_failure(client, session, monkeypatch):
    url = "https://example.org/heybox.xml"
    feed = b"""<rss xmlns:slash="http://purl.org/rss/1.0/modules/slash/"><channel>
        <item><title>First post</title><link>https://www.xiaoheihe.cn/app/bbs/link/first</link>
        <author>Author A</author><likes>50</likes><slash:comments>8</slash:comments></item>
        <item><title>Second post</title><link>https://www.xiaoheihe.cn/app/bbs/link/second</link>
        <slash:comments>3</slash:comments></item>
        <item><title>Another website</title><link>https://example.org/post</link></item>
    </channel></rss>"""
    calls = []
    fail = False

    async def fetch(collector, kind, target, limit):
        calls.append((kind, target, limit))
        if fail:
            raise CollectorError("source unavailable")
        return FetchResult(platform="rss", fetch_kind="rss_feed",
                           items=parse_feed(feed, target, config=collector.source_config, limit=limit))

    monkeypatch.setattr(get_settings(), "xiaoheihe_feed_url", url)
    monkeypatch.setattr(RssCollector, "fetch", fetch)
    assert (await sweep_custom_feeds(session, None))["sources"] == 1  # 无关注者也更新公共分区
    assert calls == [("feed", url, 30)]
    query = {"community": "xiaoheihe", "sort": "hot"}
    ready = (await client.get("/api/contents", params=query)).json()
    assert [item["title"] for item in ready["items"]] == ["First post", "Second post"]
    assert ready["items"][0]["metrics"] == {"like": 50, "reply": 8}
    assert ready["feed_status"]["automatic"] and not ready["feed_status"]["stale"]
    assert (await client.get("/api/contents", params={"community": "all"})).json()["total"] == 0

    await set_setting(session, "custom_feed_policy", {"open_beta": True})
    subscription = {"kind": "hot_posts", "target_value": "hot", "name": "小黑盒热帖"}
    for number in range(2):
        user = User(email=f"heybox-{number}@example.org", password_hash=hash_password("test-password"), is_verified=True)
        session.add(user)
        await session.commit()
        client.cookies.set("anisignal_session", await create_session(session, user))
        response = await client.post("/api/account/custom-feeds", json=subscription)
        assert response.status_code == 201
    assert (await client.post("/api/account/custom-feeds", json={**subscription, "target_value": "a-keyword"})).status_code == 400
    assert (await client.post("/api/account/custom-feeds", json=subscription)).status_code == 409
    source = await get_xiaoheihe_source(session)
    followers = (await session.scalars(select(CustomFeed))).all()
    assert {f.source_id for f in followers} == {source.id}
    assert source.next_run_at - source.last_success_at == timedelta(hours=2)
    assert (await sweep_custom_feeds(session, None))["new_items"] == 4
    assert len(calls) == 1

    fail = True
    source.next_run_at = utcnow() - timedelta(seconds=1)
    await session.commit()
    assert (await sweep_custom_feeds(session, None))["failed"] == 1
    stale = (await client.get("/api/contents", params=query)).json()
    assert stale["items"] == ready["items"]
    assert stale["feed_status"]["updated_at"] == ready["feed_status"]["updated_at"]
    assert stale["feed_status"]["stale"]
