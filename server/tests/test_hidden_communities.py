"""账号屏蔽游戏分区：只影响该账号在网站上看到的跨分区聚合列表。"""

from sqlalchemy import select

from app.models import Product, User
from app.services.ingest import ingest_items
from tests.test_account_api import _register_and_login
from tests.test_community import _video

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
    assert resp.status_code == 200 and resp.json() == {"communities": ["blhx"]}
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
    assert (await client.get("/api/account/hidden-communities")).json() == {"communities": ["blhx"]}


async def test_hidden_sections_validation(client, session):
    assert (await client.put("/api/account/hidden-communities", json={"communities": ["blhx"]})).status_code == 401
    await _register_and_login(client, session, email="hide-valid@t.com")
    bad = await client.put("/api/account/hidden-communities", json={"communities": ["nope"]})
    assert bad.status_code == 400
    from app.communities import COMMUNITIES
    everything = await client.put("/api/account/hidden-communities", json={"communities": list(COMMUNITIES)})
    assert everything.status_code == 400 and "至少保留" in everything.json()["detail"]
    assert (await client.put("/api/account/hidden-communities", json={"communities": []})).json() == {"communities": []}
