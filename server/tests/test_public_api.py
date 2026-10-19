"""公开 API 单测：内容流（过滤/搜索/摘要）、来源健康（不泄露内部字段）、统计。"""

from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import CollectTarget, ContentVersion, MetricSnapshot, RawContent
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


async def test_public_trending_ranks_by_view_delta(client, session):
    await _seed(session)
    raw_id = (
        await session.execute(select(RawContent.id).where(RawContent.external_id == "BV1Mcgp6ME1x"))
    ).scalar_one()
    now = utcnow()
    session.add_all(
        [
            MetricSnapshot(raw_content_id=raw_id, metrics={"view": 1000}, captured_at=now - timedelta(hours=12)),
            MetricSnapshot(raw_content_id=raw_id, metrics={"view": 45000}, captured_at=now - timedelta(hours=6)),
            MetricSnapshot(raw_content_id=raw_id, metrics={"view": 90000}, captured_at=now - timedelta(hours=1)),
        ]
    )
    await session.commit()

    resp = await client.get("/api/trending", params={"hours": 24})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == raw_id
    # 种子自身入库时也有一条快照，增量应落在 89000~90000 区间
    assert 89000 <= body["items"][0]["delta_view"] <= 90000
    assert body["items"][0]["metrics"]["view"] == 90000

    # ≥2 快照规则：只有 1 个快照的内容（kuro 公告）不得上榜
    kuro_id = (
        await session.execute(select(RawContent.id).where(RawContent.external_id == "50553"))
    ).scalar_one()
    session.add(
        MetricSnapshot(raw_content_id=kuro_id, metrics={"view": 999999}, captured_at=utcnow())
    )
    await session.commit()
    resp = await client.get("/api/trending", params={"hours": 24})
    assert [i["id"] for i in resp.json()["items"]] == [raw_id]


async def test_public_trending_prescreens_candidates(client, session):
    """候选超过预筛下限时仍能取到真正的飙升项，且不再全窗口载入 ORM（PERF-013）。"""
    from app.services.curation import PRESCREEN_FLOOR

    await _seed(session)
    now = utcnow()
    rows: list[MetricSnapshot] = []
    # 灌满预筛下限的普通候选：每条 2 个快照、增量 10
    for index in range(PRESCREEN_FLOOR + 10):
        raw = RawContent(
            platform="bilibili",
            content_type="video",
            external_id=f"BVprescreen{index}",
            url=f"https://www.bilibili.com/video/BVprescreen{index}",
            community="wuwa",
        )
        session.add(raw)
        await session.flush()
        session.add(
            ContentVersion(
                raw_content_id=raw.id, version=1, title=f"预筛候选 {index}", text="正文"
            )
        )
        rows.append(
            MetricSnapshot(
                raw_content_id=raw.id, metrics={"view": 100},
                captured_at=now - timedelta(hours=5, minutes=index),
            )
        )
        rows.append(
            MetricSnapshot(
                raw_content_id=raw.id, metrics={"view": 110},
                captured_at=now - timedelta(hours=4, minutes=index),
            )
        )
    # 真正的飙升项：快照最新（排在预筛结果前面），增量远超其他候选
    spike = RawContent(
        platform="bilibili",
        content_type="video",
        external_id="BVspike",
        url="https://www.bilibili.com/video/BVspike",
        community="wuwa",
    )
    session.add(spike)
    await session.flush()
    session.add(
        ContentVersion(raw_content_id=spike.id, version=1, title="真正飙升的内容", text="正文")
    )
    rows.append(
        MetricSnapshot(raw_content_id=spike.id, metrics={"view": 10}, captured_at=now - timedelta(hours=3))
    )
    rows.append(
        MetricSnapshot(raw_content_id=spike.id, metrics={"view": 90000}, captured_at=now - timedelta(minutes=1))
    )
    session.add_all(rows)
    await session.commit()

    resp = await client.get("/api/trending", params={"hours": 24, "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["items"][0]["id"] == spike.id
    assert body["items"][0]["delta_view"] == 89990


async def test_public_rss_feed(client, session):
    await _seed(session)
    resp = await client.get("/api/rss.xml")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["content-type"]
    assert '<rss version="2.0">' in resp.text
    assert "<item>" in resp.text
    assert "BV1Mcgp6ME1x" in resp.text


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


async def test_official_links_prefers_ai_labels(client, session, admin_headers):
    """AI 审核提取的链接带中文名称且优先展示；AI 决定不收的链接不再走规则回退。"""
    from copy import deepcopy

    from sqlalchemy import select

    from app.models import ContentReview, RawContent
    from app.services.ingest import ingest_items

    notice = deepcopy(KURO_ITEM)
    notice["entry"] = dict(notice["entry"], id="ai-link-hero")
    notice["detail"] = {
        "noticeId": 50556,
        "textContent": (
            '<p>活动详情 <a href="https://aki-gm-resources.aki-game.net/h5/contest">H5 活动</a></p>'
            "<p>奖励领取：https://aki-gm-resources.aki-game.net/reward，</p>"
        ),
    }
    await ingest_items(session, "kuro_notices", [notice])
    raw = await session.scalar(select(RawContent).where(RawContent.external_id == "ai-link-hero"))
    session.add(
        ContentReview(
            raw_content_id=raw.id,
            relevant=True,
            promotional=False,
            violation=False,
            quality_score=80,
            model="test",
            links=[{"url": "https://aki-gm-resources.aki-game.net/h5/contest", "label": "H5 签到活动"}],
        )
    )
    await session.commit()
    # 与生产一致：审核写入新链接后失效聚合缓存
    from app.api.public import invalidate_official_links_cache

    invalidate_official_links_cache()

    resp = await client.get("/api/official-links")
    by_url = {item["url"]: item for item in resp.json()["items"]}
    assert by_url["https://aki-gm-resources.aki-game.net/h5/contest"]["label"] == "H5 签到活动"
    # AI 决定只收 H5 链接，奖励链接不应经规则回退混入
    assert "https://aki-gm-resources.aki-game.net/reward" not in by_url


async def test_product_list_includes_latest_candidate(client, session):
    """商品列表带最新候选信号摘要（未核验），与三轴"暂无"并存。"""
    from app.models import Product, ProductSignal, ProductSignalEvidence
    from app.services.ingest import ingest_items
    from tests.test_parsers_bilibili import VIDEO_RAW

    product = Product(slug="latest-cand", name="最新候选测试键盘", watch_terms=["最新候选"])
    session.add(product)
    await session.flush()
    await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW, bvid="BVcand", title="最新候选测试键盘到货")])
    raw = await session.scalar(select(RawContent).where(RawContent.external_id == "BVcand"))
    signal = ProductSignal(
        product_id=product.id,
        signal_type="consumer_received",
        actor_type="user",
        review_status="candidate",
        summary="最新候选测试键盘到货",
        details={},
        source_method="rule",
        active=True,
    )
    session.add(signal)
    await session.flush()
    session.add(
        ProductSignalEvidence(
            signal_id=signal.id,
            raw_content_id=raw.id,
            claim_quote="到货",
            acquisition_type="unknown",
            independent_source_key="user-1",
        )
    )
    await session.commit()

    resp = await client.get("/api/products")
    item = next(i for i in resp.json()["items"] if i["slug"] == "latest-cand")
    assert item["latest_candidate"]["summary"] == "最新候选测试键盘到货"
    assert item["latest_candidate"]["content_id"] == raw.id


async def test_official_links_ai_path_requires_official_identity(client, session):
    """AI 提取链接只收官方身份内容：用户内容里的网盘/二创链接不得混入官方链接面板。"""
    from copy import deepcopy

    from sqlalchemy import select

    from app.models import ContentReview, RawContent
    from app.services.ingest import ingest_items

    # 官方公告（库洛，official）
    notice = deepcopy(KURO_ITEM)
    notice["entry"] = dict(notice["entry"], id="ai-link-official")
    notice["detail"] = {"noticeId": 50557, "textContent": "<p>活动详情</p>"}
    await ingest_items(session, "kuro_notices", [notice])
    # 用户视频（B 站普通用户）
    await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW, bvid="BVlinkuser", title="资源分享帖")])

    official_raw = await session.scalar(
        select(RawContent).where(RawContent.external_id == "ai-link-official")
    )
    user_raw = await session.scalar(select(RawContent).where(RawContent.external_id == "BVlinkuser"))
    session.add_all(
        [
            ContentReview(
                raw_content_id=official_raw.id,
                relevant=True,
                model="test",
                links=[
                    # AI 提取了一个不可读的英文标签 → 应回退为来源内容标题
                    {"url": "https://wutheringwaves.kurogames.com/en/main/news/detail/999", "label": "information"},
                ],
            ),
            ContentReview(
                raw_content_id=user_raw.id,
                relevant=True,
                model="test",
                links=[
                    {"url": "https://pan.quark.cn/s/noise", "label": "夸克网盘资源链接"},
                    {"url": "https://www.deviantart.com/someartist", "label": "作者DeviantArt主页"},
                ],
            ),
        ]
    )
    await session.commit()
    from app.api.public import invalidate_official_links_cache

    invalidate_official_links_cache()

    resp = await client.get("/api/official-links")
    items = resp.json()["items"]
    urls = [item["url"] for item in items]
    # 官方内容的链接保留，用户内容的网盘/二创链接不出现
    assert "https://wutheringwaves.kurogames.com/en/main/news/detail/999" in urls
    assert "https://pan.quark.cn/s/noise" not in urls
    assert "https://www.deviantart.com/someartist" not in urls
    # 不可读标签回退为来源内容标题（而不是 "information"）
    by_url = {item["url"]: item for item in items}
    assert by_url["https://wutheringwaves.kurogames.com/en/main/news/detail/999"]["label"] != "information"
    assert by_url["https://wutheringwaves.kurogames.com/en/main/news/detail/999"]["title"]


async def test_public_output_drops_non_http_urls(client, session):
    """SEC-035：采集来的 javascript:/data:/内网 URL 不得原样进公开响应。

    这些字段前端有 19 处裸 `<a :href>` / `<img :src>` 直接绑定，采集层与解析层都不校验
    scheme，schema 是最后一个统一收口的点。
    """
    await _seed(session)
    from sqlalchemy import update

    from app.models import ContentVersion, RawContent

    # 直接改库，模拟"上游返回了脏 URL"（复现思路与审计报告一致）。
    # url 在 raw_contents 上，media/封面在 content_versions.normalized_json 上。
    dirty = {
        "title": "脏 URL 公告",
        "url": "javascript:alert(document.cookie)",
        "media": [
            "https://example.com/banner.png",  # 正常项必须保留
            "data:text/html,<script>alert(1)</script>",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "//evil.com/x.png",
        ],
    }
    await session.execute(
        update(RawContent)
        .where(RawContent.external_id == "50553")
        .values(url="javascript:alert(document.cookie)")
    )
    await session.execute(
        update(ContentVersion)
        .where(ContentVersion.raw_content_id == RawContent.id)
        .where(RawContent.external_id == "50553")
        .values(normalized_json=dirty)
    )
    await session.commit()

    resp = await client.get("/api/contents")
    assert resp.status_code == 200
    blob = str(resp.json())
    assert "javascript:" not in blob, "内容流不得下发 javascript: URL"
    assert "data:text/html" not in blob, "内容流不得下发 data: URL"
    assert "file:" not in blob
    item = next(i for i in resp.json()["items"] if i["external_id"] == "50553")
    assert item["url"] is None, "非法 url 必须置 None，不能留原值"
    # cover 取自 media[0]，正常项应照旧作为封面（不能被校验误杀）
    assert item["cover"] == "https://example.com/banner.png"

    detail = (await client.get(f"/api/contents/{item['id']}")).json()
    assert detail["url"] is None
    assert detail["media"] == ["https://example.com/banner.png"], "非法媒体项逐个丢掉，正常项保留"


async def test_public_output_keeps_normal_urls(client, session):
    """SEC-035 的正面：正常 http(s) URL 一个都不能被误杀。"""
    await _seed(session)
    detail = (await client.get("/api/contents/1")).json()
    if detail.get("url"):
        assert detail["url"].startswith("https://")
    resp = await client.get("/api/contents")
    for item in resp.json()["items"]:
        if item["url"]:
            assert item["url"].startswith("https://"), item["url"]
        if item["cover"]:
            assert item["cover"].startswith("http"), item["cover"]
