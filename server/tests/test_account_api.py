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
    resp = await client.get("/api/account/overview")
    body = resp.json()
    assert body["keywords_total"] == 1
    assert body["keywords_enabled"] == 0  # 已停用
    assert body["subscription"]["plan"] == "free"

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
    await client.post("/api/account/keywords", json={"keyword": "爱弥斯"})

    resp = await client.get("/api/account/feed")
    body = resp.json()
    assert body["total"] == 1
    assert "爱弥斯" in body["items"][0]["title"]
    assert body["items"][0]["external_id"] == "BVf1"

    # 概览统计同步
    resp = await client.get("/api/account/overview")
    assert resp.json()["feed_total"] == 1


async def test_account_requires_login(client):
    # 清 cookie 模拟未登录
    client.cookies.clear()
    resp = await client.get("/api/account/overview")
    assert resp.status_code == 401
