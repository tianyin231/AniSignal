"""账号屏蔽游戏分区与来源平台：只影响该账号在网站上看到的列表。"""

from sqlalchemy import select, update

from app.models import Product, RawContent, User
from app.services.ingest import ingest_items
from tests.test_account_api import _register_and_login
from tests.test_community import _video
from tests.test_unified_news import snapshot, unified  # noqa: F401 - pytest 夹具

AGGREGATE_ENDPOINTS = ["/api/highlights", "/api/trending", "/api/calendar", "/api/codes", "/api/events", "/api/products"]


async def _seed(session):
    await ingest_items(session, "bilibili_search", [_video("BVhw1", "鸣潮内容")], community="wuwa")
    await ingest_items(session, "bilibili_search", [_video("BVhb1", "碧蓝航线内容")], community="blhx")
    session.add_all([Product(slug="hidden-wuwa", name="鸣潮商品", community="wuwa"),
                     Product(slug="hidden-blhx", name="碧蓝商品", community="blhx")])
    await session.commit()


async def _titles(client, **params):
    resp = await client.get("/api/contents", params={"community": "all", **params})
    assert resp.status_code == 200
    return {item["title"] for item in resp.json()["items"]}, resp.json()["total"]


async def _products(client, community="all"):
    resp = await client.get("/api/products", params={"community": community})
    return {item["name"] for item in resp.json()["items"]}


async def test_hidden_section_only_affects_that_accounts_aggregate_views(client, session):
    await _seed(session)
    await _register_and_login(client, session, email="hide-a@t.com")
    resp = await client.put("/api/account/hidden-communities", json={"communities": ["blhx"]})
    assert resp.status_code == 200 and resp.json() == {"communities": ["blhx"], "platforms": []}
    assert client.cookies.get("anisignal_hidden") == "blhx"

    assert await _titles(client) == ({"鸣潮内容"}, 1)
    assert await _products(client) == {"鸣潮商品"}
    # 显式打开被屏蔽的分区时照常显示。
    blhx = await client.get("/api/contents", params={"community": "blhx"})
    assert [item["title"] for item in blhx.json()["items"]] == ["碧蓝航线内容"]
    assert await _products(client, "blhx") == {"碧蓝商品"}
    for path in AGGREGATE_ENDPOINTS:
        resp = await client.get(path, params={"community": "all"})
        assert resp.status_code == 200, path
        assert '"blhx"' not in resp.text, path
    # RSS 在进程内共享缓存，不跟随账号屏蔽。
    rss = await client.get("/api/rss.xml", params={"community": "all"})
    assert rss.status_code == 200 and "碧蓝航线内容" in rss.text

    # 退出后访客视图不受影响，镜像 Cookie 被清除。
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert client.cookies.get("anisignal_hidden") is None
    assert await _titles(client) == ({"鸣潮内容", "碧蓝航线内容"}, 2)

    # 另一个账号没有屏蔽。
    await _register_and_login(client, session, email="hide-b@t.com")
    assert client.cookies.get("anisignal_hidden") is None
    assert await _titles(client) == ({"鸣潮内容", "碧蓝航线内容"}, 2)

    # 重新登录账号 A，屏蔽随账号恢复。
    await client.post("/api/auth/logout")
    login = await client.post("/api/auth/login", json={"email": "hide-a@t.com", "password": "password123"})
    assert login.status_code == 200
    assert client.cookies.get("anisignal_hidden") == "blhx"
    assert await _titles(client) == ({"鸣潮内容"}, 1)


async def test_leftover_mirror_cookie_without_session_does_not_filter(client, session):
    await _seed(session)
    client.cookies.set("anisignal_hidden", "blhx", domain="test.local")
    assert await _titles(client) == ({"鸣潮内容", "碧蓝航线内容"}, 2)
    # 会话检查发现未登录时清掉残留镜像。
    resp = await client.get("/api/auth/session")
    assert resp.json()["user"] is None
    assert client.cookies.get("anisignal_hidden") is None


async def test_session_check_restores_mirror_from_account(client, session):
    await _register_and_login(client, session, email="hide-sync@t.com")
    await client.put("/api/account/hidden-communities", json={"communities": ["genshin", "blhx"]})
    # 分区按固定顺序保存，与提交顺序无关。
    assert client.cookies.get("anisignal_hidden") == "blhx.genshin"
    client.cookies.set("anisignal_hidden", "wuwa", domain="test.local")
    await client.get("/api/auth/session")
    assert client.cookies.get("anisignal_hidden") == "blhx.genshin"


async def test_hidden_sections_and_feed_preferences_do_not_overwrite_each_other(client, session):
    await _register_and_login(client, session, email="hide-prefs@t.com")
    await client.put("/api/account/feed-preferences", json={"communities": ["wuwa"], "exclude_keywords": ["二创"]})
    user = await session.scalar(select(User).where(User.email == "hide-prefs@t.com"))
    await session.refresh(user)
    updated_at = user.feed_preferences["updated_at"]

    await client.put("/api/account/hidden-communities", json={"communities": ["blhx"]})
    await session.refresh(user)
    # 屏蔽不改变邮件早报的筛选，不能刷新 updated_at。
    assert user.feed_preferences["updated_at"] == updated_at
    assert user.feed_preferences["communities"] == ["wuwa"]

    await client.put("/api/account/feed-preferences", json={"communities": [], "exclude_keywords": []})
    assert (await client.get("/api/account/hidden-communities")).json() == {"communities": ["blhx"], "platforms": []}


async def test_hidden_sections_validation(client, session):
    assert (await client.put("/api/account/hidden-communities", json={"communities": ["blhx"]})).status_code == 401
    await _register_and_login(client, session, email="hide-valid@t.com")
    bad = await client.put("/api/account/hidden-communities", json={"communities": ["nope"]})
    assert bad.status_code == 400
    from app.communities import COMMUNITIES
    everything = await client.put("/api/account/hidden-communities", json={"communities": list(COMMUNITIES)})
    assert everything.status_code == 400 and "至少保留" in everything.json()["detail"]
    assert (await client.put("/api/account/hidden-communities", json={"communities": []})).json() == {"communities": [], "platforms": []}


async def test_platform_blocks_are_split_between_games_and_ai(client, session):
    await ingest_items(session, "bilibili_search", [_video("BVpw1", "鸣潮B站")], community="wuwa")
    await ingest_items(session, "bilibili_search", [_video("BVpa1", "AI B站")], community="ai")
    # AI 内容入库后待审核，这里直接放行。
    await session.execute(update(RawContent).where(RawContent.community == "ai").values(hidden=False))
    await session.commit()
    await _register_and_login(client, session, email="hide-p@t.com")
    resp = await client.put("/api/account/hidden-communities", json={"platforms": ["ai:bilibili"]})
    assert resp.json() == {"communities": [], "platforms": ["ai:bilibili"]}
    assert client.cookies.get("anisignal_hidden") == "ai:bilibili"
    # 只屏蔽 AI 快讯里的 B站：游戏分区的 B站内容照常显示，聚合与 AI 分区都不再出现 AI 的 B站内容。
    assert (await _titles(client))[0] == {"鸣潮B站"}
    resp = await client.get("/api/contents", params={"community": "ai"})
    assert resp.json()["items"] == []
    # 显式选中平台时照常显示。
    resp = await client.get("/api/contents", params={"community": "ai", "platform": "bilibili"})
    assert [item["title"] for item in resp.json()["items"]] == ["AI B站"]
    for path in ["/api/highlights", "/api/trending"]:
        assert "AI B站" not in (await client.get(path, params={"community": "all"})).text, path

    # 分区与平台可同时设置；只改其中一项时另一项保留。
    resp = await client.put("/api/account/hidden-communities", json={"communities": ["blhx"]})
    assert resp.json() == {"communities": ["blhx"], "platforms": ["ai:bilibili"]}
    assert client.cookies.get("anisignal_hidden") == "blhx.ai:bilibili"
    resp = await client.put("/api/account/hidden-communities", json={"platforms": ["game:bilibili", "ai:bilibili"]})
    assert resp.json()["platforms"] == ["game:bilibili", "ai:bilibili"]
    assert (await _titles(client))[0] == set()
    resp = await client.get("/api/contents", params={"community": "wuwa"})
    assert resp.json()["items"] == []
    assert (await client.put("/api/account/hidden-communities", json={"platforms": ["game:weibo"]})).status_code == 400
    assert (await client.put("/api/account/hidden-communities", json={"platforms": "ai:x"})).status_code == 400

    # 退出后恢复访客视图。
    await client.post("/api/auth/logout")
    assert (await _titles(client))[0] == {"鸣潮B站", "AI B站"}


async def test_ai_feed_respects_blocked_platforms(client, session, snapshot, unified):
    from app.services.news_import import import_news

    await import_news(session, snapshot)
    before = (await client.get("/api/ai-news/events")).json()
    assert {item["platform"] for item in before["items"]} == {"bilibili", "rss"}
    await _register_and_login(client, session, email="hide-ai@t.com")
    await client.put("/api/account/hidden-communities", json={"platforms": ["ai:bilibili"]})
    events = (await client.get("/api/ai-news/events")).json()
    assert events["total"] == 1 and [item["platform"] for item in events["items"]] == ["rss"]
    listing = (await client.get("/api/ai-news")).json()
    assert {item["platform"] for item in listing["items"]} == {"rss"}
    # 热榜走事件归组：B站报道在归组前剔除，事件只保留其他来源。
    from app.services.news_publication import list_stories

    platforms = lambda stories: sorted(s["platform"] for story in stories for s in story["sources"])  # noqa: E731
    assert platforms(await list_stories(session)) == ["bilibili", "rss"]
    assert platforms(await list_stories(session, exclude_platforms=("bilibili",))) == ["rss"]
    # 游戏分区的 B站屏蔽不影响 AI 快讯。
    await client.put("/api/account/hidden-communities", json={"platforms": ["game:bilibili"]})
    assert (await client.get("/api/ai-news/events")).json()["total"] == 2
