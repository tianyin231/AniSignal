from contextlib import asynccontextmanager
from datetime import timedelta, timezone

from sqlalchemy import select

from app.clock import utcnow
from app.collectors.base import CollectorError
from app.collectors.rss import RssCollector, parse_feed
from app.collectors import xiaoheihe as account_collector
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


async def test_xiaoheihe_account_links_resolve_and_share_one_poll(client, session, monkeypatch):
    calls = []
    alias = "abc012def345"
    now = utcnow()

    async def public_posts(http, url, *, params, retries, timeout):
        assert "cookie" not in http.headers and "authorization" not in http.headers
        uid = "42" if params["userid"] == alias else params["userid"]
        calls.append((params["userid"], params["offset"], params["limit"], retries))
        post = {
            "userid": int(uid),
            "title": f"账号 {uid} 的帖子",
            "description": f"正文摘要 {uid}",
            "imgs": [f"https://imgheybox.max-c.com/post/{uid}.jpg"],
            "click": 1234,
            "comment_num": 56,
            "link_award_num": 78,
            "share_url": f"https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=post{uid}",
            "create_at": str(int(now.replace(tzinfo=timezone.utc).timestamp())),
        }
        return {"status": "ok", "result": {"user": {"userid": int(uid), "username": "公开作者"},
                                            "post_links": [post, {**post, "userid": 9999}]}}

    @asynccontextmanager
    async def slot(*args):
        yield

    monkeypatch.setattr(account_collector, "get_json", public_posts)
    monkeypatch.setattr("app.services.custom_feeds.platform_slot", slot)
    await set_setting(session, "custom_feed_policy", {"open_beta": True})
    path = "/api/account/custom-feeds"
    for number, target in enumerate(("00042", f"https://xiaoheihe.cn/app/user/profile/{alias}?from=share")):
        user = User(email=f"xhh-account-{number}@example.org", password_hash=hash_password("test-password"), is_verified=True)
        session.add(user)
        await session.commit()
        client.cookies.set("anisignal_session", await create_session(session, user))
        response = await client.post(path, json={"name": "作者监控", "kind": "xhh_account", "target_value": target})
        assert response.status_code == 201, response.text

    assert (await client.post(path, json={"name": "另一个账号", "kind": "xhh_account", "target_value": "56"})).status_code == 201
    # 未配置外部热帖/关键词源时，拒绝创建空壳监控，不再假报成功空转
    assert (await client.post(path, json={"name": "热帖", "kind": "hot_posts", "target_value": "hot"})).status_code == 400
    assert (await client.post(path, json={"name": "关键词", "kind": "xhh_keyword", "target_value": "鸣潮"})).status_code == 400
    assert (await client.post(path, json={"name": "错域名", "kind": "xhh_account",
        "target_value": "https://xiaoheihe.cn.example.org/app/user/profile/42"})).status_code == 400
    watchers = (await session.scalars(select(CustomFeed).where(CustomFeed.target_value == "42").order_by(CustomFeed.id))).all()
    assert len(watchers) == 2 and watchers[0].source_id == watchers[1].source_id
    assert (await sweep_custom_feeds(session, None))["new_items"] == 3
    assert sorted(calls) == [("42", 0, 20, 1), ("56", 0, 20, 1), (alias, 0, 1, 0)]
    detail = (await client.get(f"/api/custom/feeds/{watchers[-1].id}")).json()
    assert detail["source_url"] == "https://www.xiaoheihe.cn/app/user/profile/42"
    assert len(detail["items"]) == 1 and detail["items"][0]["published_at"]
    assert detail["items"][0]["url"] == "https://www.xiaoheihe.cn/app/bbs/link/post42"
    assert detail["items"][0]["cover"] == "https://imgheybox.max-c.com/post/42.jpg"
    assert detail["items"][0]["view"] == 1234
    assert (await client.get("/api/contents", params={"community": "xiaoheihe"})).json()["total"] == 0


async def test_xiaoheihe_standard_targets_and_bridge_keyword_pipeline(
    client, session, session_factory, admin_headers, monkeypatch
):
    from app.models import Event
    from app.services.collect import run_collection

    now = utcnow()
    seen_auth: list[str | None] = []

    async def fake_get_json(http, url, *, params, retries, timeout):
        seen_auth.append(http.headers.get("authorization"))
        if url.endswith("/bbs/app/profile/user/link/list_v2"):
            uid = "888"
            return {
                "status": "ok",
                "result": {
                    "user": {"userid": 888, "username": "小黑盒攻略作者"},
                    "post_links": [
                        {
                            "userid": 888,
                            "title": "终末地配队实测",
                            "description": "这是完整的正文内容，带图文与指标。",
                            "imgs": ["https://imgheybox.max-c.com/guide/1.jpg"],
                            "click": 8500,
                            "comment_num": 42,
                            "link_award_num": 99,
                            "share_url": "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=acc888a",
                            "create_at": str(int(now.replace(tzinfo=timezone.utc).timestamp())),
                        }
                    ],
                },
            }
        if url.endswith("/v1/search"):
            return {
                "items": [
                    {
                        "external_id": "kwlow1",
                        "title": "低热帖子",
                        "description": "阅读量不够门槛",
                        "click": 100,
                        "comment_num": 1,
                        "link_award_num": 2,
                        "author": {"external_id": "101", "name": "玩家甲"},
                        "published_at": now.isoformat(),
                    },
                    {
                        "external_id": "kwhigh2",
                        "title": "兑换码汇总帖",
                        "description": "高阅读关键词命中帖子",
                        "imgs": ["https://imgheybox.max-c.com/kw/2.jpg"],
                        "click": 5000,
                        "comment_num": 30,
                        "link_award_num": 60,
                        "author": {"external_id": "102", "name": "玩家乙"},
                        "published_at": now.isoformat(),
                    },
                ]
            }
        raise AssertionError(f"unexpected url: {url}")

    @asynccontextmanager
    async def slot(*args):
        yield

    monkeypatch.setattr(account_collector, "get_json", fake_get_json)
    monkeypatch.setattr("app.services.collect.SessionLocal", session_factory)
    monkeypatch.setattr("app.services.collect.platform_slot", slot)
    monkeypatch.setattr("app.services.custom_feeds.platform_slot", slot)

    # 1. 后台热配置小黑盒独立网关（Token 脱敏且留空保留）
    saved = await client.put(
        "/api/admin/settings/xiaoheihe",
        headers=admin_headers,
        json={"bridge_url": "https://xhh-bridge.example.com/", "bridge_token": "secret-token-1", "feed_url": ""},
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "bridge_url": "https://xhh-bridge.example.com",
        "bridge_token_set": True,
        "feed_url": "",
        "keyword_enabled": True,
        "hot_enabled": True,
    }
    keep = await client.put(
        "/api/admin/settings/xiaoheihe",
        headers=admin_headers,
        json={"bridge_url": "https://xhh-bridge.example.com", "bridge_token": "", "feed_url": ""},
    )
    assert keep.status_code == 200 and keep.json()["bridge_token_set"] is True

    # 2. 通过标准 /api/admin/collect-targets 给小黑盒独立分区配置账号与关键词目标
    acc_target = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "xiaoheihe",
            "target_type": "account_dynamics",
            "target_value": "https://www.xiaoheihe.cn/app/user/profile/888",
            "community": "xiaoheihe",
            "display_name": "攻略作者",
        },
    )
    assert acc_target.status_code == 201, acc_target.text
    assert acc_target.json()["target_value"] == "888"

    kw_target = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "xiaoheihe",
            "target_type": "keyword_search",
            "target_value": "兑换码",
            "community": "xiaoheihe",
            "min_views": 1000,
        },
    )
    assert kw_target.status_code == 201, kw_target.text
    assert kw_target.json()["min_views"] == 1000

    # 3. 跑标准主采集管线（隔离在 domain="feed"，不污染主游戏事件链与聚合首页）
    r1 = await run_collection(None, acc_target.json()["id"])
    r2 = await run_collection(None, kw_target.json()["id"])
    assert r1["status"] == "success" and r1["items_new"] == 1
    assert r2["status"] == "success" and r2["items_new"] == 1  # 低热帖子被 min_views 过滤
    assert "Bearer secret-token-1" in seen_auth

    listing = (await client.get("/api/contents", params={"community": "xiaoheihe", "sort": "hot"})).json()
    assert listing["total"] == 2
    assert [i["title"] for i in listing["items"]] == ["终末地配队实测", "兑换码汇总帖"]
    assert listing["items"][0]["cover"] == "https://imgheybox.max-c.com/guide/1.jpg"
    assert listing["items"][0]["text_excerpt"].startswith("这是完整的正文内容")
    assert listing["items"][0]["metrics"] == {"view": 8500, "reply": 42, "like": 99}
    assert listing["feed_status"]["automatic"] is True

    # 支持搜索与分页，且完全隔离于首页聚合与游戏事件链
    searched = (await client.get("/api/contents", params={"community": "xiaoheihe", "q": "兑换码"})).json()
    assert [i["title"] for i in searched["items"]] == ["兑换码汇总帖"]
    assert (await client.get("/api/contents", params={"community": "all"})).json()["total"] == 0
    assert (await session.scalars(select(Event))).all() == []
    assert (await sweep_extractions(session))["candidates"] == 0

    # 4. 个人定制监控支持 xhh_keyword（与 B 站 keyword 完全对齐：min_views + 过滤词 + 共享抓取）
    await set_setting(session, "custom_feed_policy", {"open_beta": True})
    user = User(email="xhh-kw@example.org", password_hash=hash_password("test-password"), is_verified=True)
    session.add(user)
    await session.commit()
    client.cookies.set("anisignal_session", await create_session(session, user))
    quota = (await client.get("/api/account/custom-quota")).json()
    assert quota["xiaoheihe_keyword_enabled"] is True and quota["xiaoheihe_hot_enabled"] is True

    created = await client.post(
        "/api/account/custom-feeds",
        json={"name": "小黑盒兑换码", "kind": "xhh_keyword", "target_value": "兑换码", "min_views": 1000},
    )
    assert created.status_code == 201, created.text
    assert (await sweep_custom_feeds(session, None))["new_items"] == 1
    detail = (await client.get(f"/api/custom/feeds/{created.json()['id']}")).json()
    assert [item["title"] for item in detail["items"]] == ["兑换码汇总帖"]
    assert detail["items"][0]["view"] == 5000
    assert detail["items"][0]["cover"] == "https://imgheybox.max-c.com/kw/2.jpg"
