"""内容详情端点单测：正文/消毒/指标历史/关联事件。"""

from app.services.events import run_aggregation
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import VIDEO_RAW
from tests.test_parsers_others import KURO_ITEM


async def test_detail_plain_text_and_metrics(client, session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    listing = (await client.get("/api/contents")).json()
    cid = listing["items"][0]["id"]

    resp = await client.get(f"/api/contents/{cid}")
    assert resp.status_code == 200
    d = resp.json()
    assert d["external_id"] == "BV1Mcgp6ME1x"
    assert d["title"] == "视频标题"
    assert d["text"] == "视频简介"
    assert d["text_html"] is None  # 纯文本不做 HTML 渲染
    assert d["identity"] == "unknown"
    assert d["metrics"]["view"] == 680
    assert len(d["metrics_history"]) >= 1
    assert d["media"] == ["https://i1.hdslb.com/bfs/archive/example.jpg"]
    assert d["url"]  # 原文链接保留


async def test_detail_html_sanitized(client, session):
    await ingest_items(session, "kuro_notices", [KURO_ITEM])
    listing = (await client.get("/api/contents", params={"platform": "kuro_global"})).json()
    cid = listing["items"][0]["id"]

    resp = await client.get(f"/api/contents/{cid}")
    d = resp.json()
    assert d["text_html"] == "<p>html body</p>"  # 安全标签保留


async def test_detail_sanitizes_dangerous_html(client, session):
    evil = dict(
        KURO_ITEM,
        detail={"noticeId": 666, "textContent": '<p onclick="x()">hi</p><script>alert(1)</script><img src=x onerror=alert(2)>'},
        entry={"id": "666", "tabTitle": {"en": "evil"}},
    )
    await ingest_items(session, "kuro_notices", [evil])
    listing = (await client.get("/api/contents", params={"platform": "kuro_global"})).json()
    cid = listing["items"][0]["id"]

    resp = await client.get(f"/api/contents/{cid}")
    html = resp.json()["text_html"]
    assert "script" not in html
    assert "onclick" not in html
    assert "onerror" not in html
    assert "<p>" in html  # 合法标签保留


async def test_detail_links_event(client, session):
    from app.models import Product

    session.add(Product(slug="kb", name="测试键盘", watch_terms=["视频标题"]))
    await session.commit()
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    await run_aggregation(session)
    await session.commit()

    listing = (await client.get("/api/contents")).json()
    cid = listing["items"][0]["id"]
    resp = await client.get(f"/api/contents/{cid}")
    event = resp.json()["event"]
    if event:  # 若该内容成为某事件证据
        assert "id" in event and "title" in event
        # 事件源列表带 content_id，可回链站内详情
        events = (await client.get("/api/events")).json()["items"]
        for e in events:
            for source in e["sources"]:
                if source["content_id"] == cid:
                    assert source["platform"] == "bilibili"
                    return
        raise AssertionError("event sources should carry content_id")


async def test_detail_404(client):
    resp = await client.get("/api/contents/99999")
    assert resp.status_code == 404


async def test_chat_hidden_from_default_stream(client, session):
    from app.services.classify import seed_defaults
    from sqlalchemy import select
    from app.models import ClassificationRule

    await seed_defaults(session)
    await ingest_items(
        session,
        "bilibili_search",
        [
            dict(VIDEO_RAW, bvid="BVchat", title="爱弥斯同人二创小剧场", owner_mid=9, owner_name="画师"),
            dict(VIDEO_RAW, bvid="BVreal", title="爱弥斯键盘到货开箱实录", owner_mid=8, owner_name="用户"),
        ],
    )
    body = (await client.get("/api/contents")).json()
    titles = [i["title"] for i in body["items"]]
    assert "爱弥斯键盘到货开箱实录" in titles
    assert "爱弥斯同人二创小剧场" not in titles  # 闲聊默认隐藏
    # 显式包含时可查到
    body = (await client.get("/api/contents", params={"include_chat": True})).json()
    assert "爱弥斯同人二创小剧场" in [i["title"] for i in body["items"]]
