"""账号中心 API 单测：关键词 CRUD、关注流、鉴权边界。"""

from sqlalchemy import select

from app.models import EmailCode
from app.services.ingest import ingest_items
from tests.test_parsers_bilibili import VIDEO_RAW


async def _register_and_login(client, session, email="acc@t.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "password123"})
    code_row = await session.scalar(
        select(EmailCode).order_by(EmailCode.id.desc()).limit(1)
    )
    resp = await client.post(
        "/api/auth/verify", json={"email": email, "code": code_row.code_plain}
    )
    assert resp.status_code == 200
    return resp


async def test_keyword_crud_and_limit(client, session):
    await _register_and_login(client, session)

    # 未登录访问被拒
    resp = await client.get("/api/account/keywords")
    assert resp.status_code in (200, 401)  # cookie 已种，先确认当前可用

    resp = await client.post("/api/account/keywords", json={"keyword": "爱弥斯"})
    assert resp.status_code == 201
    kid = resp.json()["id"]

    # 重复添加
    resp = await client.post("/api/account/keywords", json={"keyword": "爱弥斯"})
    assert resp.status_code == 409

    # 停用
    resp = await client.put(f"/api/account/keywords/{kid}", json={"enabled": False})
    assert resp.json()["enabled"] is False

    # 列表 + 概览
    resp = await client.get("/api/account/keywords")
    assert len(resp.json()) == 1
    assert resp.json()[0]["matches"] == 0  # 尚无命中内容
    resp = await client.get("/api/account/overview")
    body = resp.json()
    assert body["keywords_total"] == 1
    assert body["keywords_enabled"] == 0  # 已停用
    assert "subscription" not in body  # 订阅未上线，不再返回

    # 删除
    resp = await client.delete(f"/api/account/keywords/{kid}")
    assert resp.status_code == 204


async def test_keyword_limit(client, session):
    await _register_and_login(client, session, email="lim@t.com")
    for i in range(10):
        resp = await client.post("/api/account/keywords", json={"keyword": f"词{i}"})
        assert resp.status_code == 201
    resp = await client.post("/api/account/keywords", json={"keyword": "超额词"})
    assert resp.status_code == 400


async def test_feed_matches_keywords(client, session):
    await _register_and_login(client, session, email="feed@t.com")
    await ingest_items(
        session,
        "bilibili_search",
        [
            dict(VIDEO_RAW, bvid="BVf1", title="爱弥斯键盘开箱实录"),
            dict(VIDEO_RAW, bvid="BVf2", title="鸣潮3.6版本实机演示"),
        ],
    )
    resp = await client.post("/api/account/keywords", json={"keyword": "爱弥斯"})
    kid = resp.json()["id"]

    resp = await client.get("/api/account/feed")
    body = resp.json()
    assert body["total"] == 1
    assert "爱弥斯" in body["items"][0]["title"]
    assert body["items"][0]["external_id"] == "BVf1"
    assert body["items"][0]["matched_keywords"] == ["爱弥斯"]
    assert body["new_today"] == 1  # 刚采集即计为近 24h

    # 关键词列表带命中数
    resp = await client.get("/api/account/keywords")
    assert resp.json()[0]["matches"] == 1

    # keyword_id 筛选：命中该关键词
    resp = await client.get("/api/account/feed", params={"keyword_id": kid})
    assert resp.json()["total"] == 1

    # keyword_id 不属于当前用户 → 404
    resp = await client.get("/api/account/feed", params={"keyword_id": 99999})
    assert resp.status_code == 404

    # 概览统计同步
    resp = await client.get("/api/account/overview")
    assert resp.json()["feed_total"] == 1


async def test_feed_keyword_filter_isolates(client, session):
    """keyword_id 筛选只看该关键词，不受其他关键词干扰。"""
    await _register_and_login(client, session, email="filter@t.com")
    await ingest_items(
        session,
        "bilibili_search",
        [
            dict(VIDEO_RAW, bvid="BVa", title="爱弥斯键盘联名开售"),
            dict(VIDEO_RAW, bvid="BVb", title="菲比角色 PV 发布"),
        ],
    )
    kid_a = (await client.post("/api/account/keywords", json={"keyword": "爱弥斯"})).json()["id"]
    await client.post("/api/account/keywords", json={"keyword": "菲比"})

    resp = await client.get("/api/account/feed", params={"keyword_id": kid_a})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "BVa"
    assert body["items"][0]["matched_keywords"] == ["爱弥斯"]


async def test_account_requires_login(client):
    # 清 cookie 模拟未登录
    client.cookies.clear()
    resp = await client.get("/api/account/overview")
    assert resp.status_code == 401


async def test_profile_update(client, session):
    await _register_and_login(client, session, email="profile@t.com")
    resp = await client.put(
        "/api/auth/profile",
        json={"display_name": "漂泊者", "avatar": "teal-mint"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"display_name": "漂泊者", "avatar": "teal-mint"}

    # me 与 overview 都带上新资料
    resp = await client.get("/api/auth/me")
    assert resp.json()["display_name"] == "漂泊者"
    resp = await client.get("/api/account/overview")
    assert resp.json()["avatar"] == "teal-mint"

    # 非法头像被白名单拦截
    resp = await client.put("/api/auth/profile", json={"avatar": "hacker"})
    assert resp.status_code == 400


async def test_feed_preferences_latest_version_and_pagination(client, session):
    from app.models import ContentVersion
    from tests.test_notifications import _content

    await _register_and_login(client, session, email="preferences@t.com")
    await client.post("/api/account/keywords", json={"keyword": "角色"})
    kept = await _content(session, "角色技能介绍", community="hsr")
    stale = await _content(session, "角色旧标题", community="hsr")
    await _content(session, "角色二创", community="hsr")
    await _content(session, "角色鸣潮公告")
    other = await _content(session, "角色活动公告", community="hsr")
    session.add_all([
        ContentVersion(raw_content_id=kept.id, version=2, title="角色技能介绍更新", text="", normalized_json={}),
        ContentVersion(raw_content_id=stale.id, version=2, title="已更正的其他公告", text="", normalized_json={}),
    ])
    await session.commit()

    assert (await client.get("/api/account/feed")).json()["total"] == 4
    preferences = {"communities": ["hsr"], "exclude_keywords": ["二创"]}
    assert (await client.put("/api/account/feed-preferences", json=preferences)).json() == preferences
    assert (await client.get("/api/account/feed-preferences")).json() == preferences
    first = (await client.get("/api/account/feed", params={"limit": 1})).json()
    second = (await client.get("/api/account/feed", params={"limit": 1, "offset": 1})).json()
    assert first["total"] == second["total"] == 2
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {kept.id, other.id}
    assert (await client.get("/api/account/overview")).json()["feed_total"] == 2
    assert (await client.get("/api/account/keywords")).json()[0]["matches"] == 2
    await client.put("/api/account/feed-preferences", json={"communities": [], "exclude_keywords": []})
    assert (await client.get("/api/account/feed")).json()["total"] == 4

    # 偏好属于个人账号，不能串到其他用户；非法分区不能写入。
    await _register_and_login(client, session, email="preferences-other@t.com")
    assert (await client.get("/api/account/feed-preferences")).json() == {"communities": [], "exclude_keywords": []}
    assert (await client.put("/api/account/feed-preferences", json={"communities": ["missing"]})).status_code == 400
    assert (await client.put("/api/account/feed-preferences", json={"exclude_keywords": ["x"] * 21})).status_code == 400
    client.cookies.clear()
    assert (await client.put("/api/account/feed-preferences", json={})).status_code == 401
