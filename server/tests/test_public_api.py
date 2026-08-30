"""公开 API 单测：内容流（过滤/搜索/摘要）、来源健康（不泄露内部字段）、统计。"""

from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import CollectTarget, RawContent
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import DYNAMIC_RAW, VIDEO_RAW
from tests.test_parsers_others import KURO_ITEM


async def _seed(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    await ingest_items(session, "kuro_notices", [KURO_ITEM])
    session.add(
        CollectTarget(
            platform="bilibili",
            target_type="keyword_search",
            target_value="鸣潮",
            display_name="B站搜索·鸣潮",
        )
    )
    await session.commit()


async def test_public_contents_no_auth(client, session):
    await _seed(session)
    resp = await client.get("/api/contents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    ids = {i["external_id"] for i in body["items"]}
    assert ids == {"BV1Mcgp6ME1x", "50553"}


async def test_public_contents_identity_filter(client, session):
    await _seed(session)
    resp = await client.get("/api/contents", params={"identity": "official"})
    body = resp.json()
    # kuro 公告 identity=official（作者 Kuro Games）；B 站视频作者 unknown
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "50553"
    assert body["items"][0]["identity"] == "official"
    # kuro 公告无指标 → metrics 为 None 不报错
    assert body["items"][0]["metrics"] is None


async def test_public_contents_search_and_fields(client, session):
    await _seed(session)
    resp = await client.get("/api/contents", params={"q": "Version Update"})
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["title"] == "Version Update Notice"
    assert item["text_excerpt"].startswith("html body")
    assert item["cover"] == "https://example.com/banner.png"
    assert item["source_name"] == "Kuro Games"
    assert item["identity"] == "official"
    assert item["category"] == "official_announce"
    assert item["preview_mode"] == "full"
    assert "<p>" not in item["text_excerpt"]

    # 平台过滤
    resp = await client.get("/api/contents", params={"platform": "bilibili"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["metrics"]["view"] == 680

    # 首页把国服、国际服官网合并为一个用户可理解的筛选项。
    resp = await client.get("/api/contents", params={"platform": "kuro"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["platform"] == "kuro_global"


async def test_public_content_marks_external_only_preview(client, session):
    await ingest_items(session, "bilibili_dynamics", [DYNAMIC_RAW])
    resp = await client.get("/api/contents", params={"identity": "official"})
    item = resp.json()["items"][0]
    assert item["preview_mode"] == "external_only"
    assert item["text_excerpt"] is None
    detail = (await client.get(f"/api/contents/{item['id']}")).json()
    assert detail["preview_mode"] == "external_only"
    assert detail["url"].startswith("https://t.bilibili.com/")


async def test_public_sources_hide_internal_details(client, session):
    await _seed(session)
    resp = await client.get("/api/sources")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    source = items[0]
    assert source["name"] == "B站搜索·鸣潮"
    assert "last_error" not in source
    assert "target_value" not in source


async def test_public_stats(client, session):
    await _seed(session)
    resp = await client.get("/api/stats")
    body = resp.json()
    assert body["total_contents"] == 2
    assert body["by_platform"] == {"bilibili": 1, "kuro_global": 1}
    assert body["official_contents"] == 1
    assert body["latest_collected_at"] is not None


async def test_public_statistics_uses_daily_aggregates(client, session):
    await _seed(session)
    resp = await client.get("/api/statistics", params={"days": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["total_contents"] == 2
    assert body["totals"]["official_contents"] == 1
    assert body["totals"]["community_contents"] == 1
    assert body["totals"]["period_new"] == 2
    assert {item["platform"] for item in body["platforms"]} == {"bilibili", "kuro_global"}
    assert len(body["trend"]) == 30


async def test_public_statistics_rejects_unbounded_range(client):
    resp = await client.get("/api/statistics", params={"days": 9999})
    assert resp.status_code == 422


async def test_public_contents_recent_window_and_identity_exclusion(client, session):
    await _seed(session)
    rows = list(await session.scalars(select(RawContent).order_by(RawContent.id)))
    rows[0].published_at = utcnow() - timedelta(days=10)
    rows[1].published_at = utcnow() - timedelta(hours=2)
    await session.commit()

    resp = await client.get(
        "/api/contents",
        params={"published_within_hours": 72, "exclude_identity": "official"},
    )
    body = resp.json()
    assert body["total"] == 0

    resp = await client.get("/api/contents", params={"published_within_hours": 72})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "50553"


async def test_public_contents_order_by_publication_not_backfill_time(client, session):
    await _seed(session)
    rows = list(await session.scalars(select(RawContent).order_by(RawContent.id)))
    older, newer = rows
    older.published_at = utcnow() - timedelta(days=365)
    older.collected_at = utcnow()
    newer.published_at = utcnow() - timedelta(hours=1)
    newer.collected_at = utcnow() - timedelta(days=1)
    await session.commit()

    body = (await client.get("/api/contents")).json()
    assert body["items"][0]["id"] == newer.id
    assert body["items"][1]["id"] == older.id


async def test_public_contents_hide_promotions_by_default(client, session):
    from app.services.classify import seed_defaults

    await seed_defaults(session)
    promo = dict(VIDEO_RAW, bvid="BVpromo", title="关注并转发抽奖")
    await ingest_items(session, "bilibili_search", [promo])

    assert (await client.get("/api/contents")).json()["total"] == 0
    visible = (await client.get("/api/contents", params={"include_chat": True})).json()
    assert visible["total"] == 1
    assert visible["items"][0]["category"] == "promotion"


async def test_wallpaper_info_returns_fresh_url(client, monkeypatch):
    from app.api import public as public_module

    urls = iter(["https://list.yppp.net/d/image/a.webp", "https://list.yppp.net/d/image/b.webp"])

    async def fake_resolve():
        return next(urls)

    monkeypatch.setattr(public_module, "_resolve_wallpaper", fake_resolve)
    first = (await client.get("/api/wallpaper")).json()
    second = (await client.get("/api/wallpaper")).json()
    assert first["url"].endswith("a.webp")
    assert second["url"].endswith("b.webp")  # 每次刷新返回不同图


async def test_wallpaper_unavailable_returns_502(client, monkeypatch):
    from app.api import public as public_module

    async def fake_resolve():
        return None

    monkeypatch.setattr(public_module, "_resolve_wallpaper", fake_resolve)
    assert (await client.get("/api/wallpaper")).status_code == 502


async def test_wallpaper_download_requires_issued_url(client, monkeypatch):
    """下载只接受服务端签发过的地址，防止沦为开放代理。"""
    import time as _time

    import httpx
    from app.api import public as public_module
    from fastapi import HTTPException

    url = "https://list.yppp.net/d/image/a.webp"

    # 未签发的地址 → 404（真实校验逻辑，不发 HTTP）
    try:
        await public_module._fetch_wallpaper_bytes("https://evil.example/x.jpg")
        raised = False
    except HTTPException as exc:
        raised = exc.status_code == 404
    assert raised

    # 签发后可下载
    public_module._wallpaper_issued[url] = _time.monotonic()

    class FakeResp:
        status_code = 200
        headers = {"content-type": "image/webp"}
        content = b"fake-image-bytes"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    resp = await client.get("/api/wallpaper/download", params={"url": url})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/webp")
    assert 'filename="anisignal-wallpaper-' in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.webp"')

    # 签发过期后 → 404
    public_module._wallpaper_issued[url] = (
        _time.monotonic() - public_module.WALLPAPER_ISSUED_TTL_SECONDS - 1
    )
    resp = await client.get("/api/wallpaper/download", params={"url": url})
    assert resp.status_code == 404


async def test_wallpaper_resolve_rejects_non_image(monkeypatch):
    """解析结果必须是图片响应，否则视为源不可用。"""
    import httpx
    from app.api import public as public_module

    public_module._wallpaper_last_url = None
    public_module._wallpaper_last_resolve = 0.0

    class FakeResp:
        status_code = 200
        url = "https://evil.example/not-image"
        headers = {"content-type": "text/html"}

        async def aclose(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        def build_request(self, *a, **k):
            return object()

        async def send(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    async with public_module._wallpaper_lock:
        pass
    assert await public_module._resolve_wallpaper() is None
    assert public_module._wallpaper_last_url is None  # 不缓存非图片结果




async def test_official_links_aggregation(client, session, admin_headers):
    """官方动态正文里的外链被聚合去重；已下架内容的外链不出现。"""
    from copy import deepcopy

    from sqlalchemy import select

    from app.models import RawContent
    from app.services.ingest import ingest_items

    notice = deepcopy(KURO_ITEM)
    notice["entry"] = dict(notice["entry"], id="link-hero")
    notice["detail"] = {
        "noticeId": 50554,
        "textContent": (
            '<p>活动详情 <a href="https://aki-gm-resources.aki-game.net/h5/contest">H5 活动</a></p>'
            "<p>奖励领取：https://aki-gm-resources.aki-game.net/reward，</p>"
            '<p>相关视频：<a href="https://www.bilibili.com/video/BVfake123">转发视频</a></p>'
        ),
    }
    await ingest_items(session, "kuro_notices", [notice])

    # 同一链接再次出现（另一条官方内容）→ 去重
    dup = deepcopy(KURO_ITEM)
    dup["entry"] = dict(notice["entry"], id="link-dup")
    dup["detail"] = dict(notice["detail"], noticeId=50555)
    await ingest_items(session, "kuro_notices", [dup])

    rows = list(await session.scalars(select(RawContent)))
    hero_id = next(r.id for r in rows if r.id)

    resp = await client.get("/api/official-links")
    assert resp.status_code == 200
    items = resp.json()["items"]
    urls = [item["url"] for item in items]
    assert "https://aki-gm-resources.aki-game.net/h5/contest" in urls
    assert "https://aki-gm-resources.aki-game.net/reward" in urls
    assert urls.count("https://aki-gm-resources.aki-game.net/h5/contest") == 1
    # 视频页面链接是内容互推，不属于活动链接
    assert not any("bilibili.com/video/" in u for u in urls)
    assert all(item["category"] == "kuro" for item in items)
    assert items[0]["title"]
    by_url = {item["url"]: item for item in items}
    assert by_url["https://aki-gm-resources.aki-game.net/h5/contest"]["label"] == "H5 活动"
    assert by_url["https://aki-gm-resources.aki-game.net/reward"]["label"] == "奖励领取"

    # 下架后外链不再出现（管理端动作会失效聚合缓存）；两条都下架（链接相同）
    for content_id in (hero_id, hero_id + 1):
        resp = await client.patch(
            f"/api/admin/contents/{content_id}/visibility",
            json={"hidden": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
    resp = await client.get("/api/official-links")
    assert "https://aki-gm-resources.aki-game.net/h5/contest" not in [
        item["url"] for item in resp.json()["items"]
    ]
