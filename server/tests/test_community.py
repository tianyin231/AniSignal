"""游戏分区（community）单测：入库归属、词典作用域、事件隔离、公开 API 过滤。"""

from sqlalchemy import func, select

from app.models import ClassificationRule, Event, Evidence, RawContent
from app.services.classify import classify_content, seed_defaults
from app.services.events import run_aggregation
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import VIDEO_RAW


def _video(bvid: str, title: str, owner_mid: int = 100, owner: str = "用户") -> dict:
    return dict(VIDEO_RAW, bvid=bvid, title=title, owner_mid=owner_mid, owner_name=owner, description="")


async def test_ingest_assigns_community(session):
    await ingest_items(session, "bilibili_search", [_video("BVw1", "鸣潮视频")], community="wuwa")
    await ingest_items(session, "bilibili_search", [_video("BVb1", "碧蓝航线视频")], community="blhx")
    rows = {r.external_id: r.community for r in (await session.execute(select(RawContent))).scalars()}
    assert rows["BVw1"] == "wuwa"
    assert rows["BVb1"] == "blhx"


async def test_classification_rule_community_scope(session):
    await seed_defaults(session)
    # 分区专属规则：碧蓝航线专属词只作用于 blhx，鸣潮专属词只作用于 wuwa
    session.add_all(
        [
            ClassificationRule(category="review", pattern="舰船强化", priority=1, community="blhx"),
            ClassificationRule(category="review", pattern="声骸强化", priority=1, community="wuwa"),
        ]
    )
    await session.commit()
    rules = list((await session.execute(select(ClassificationRule))).scalars())
    # 通用规则（community=NULL）对两个分区都生效
    assert classify_content(_norm("键盘开箱"), rules, "blhx") == "unboxing"
    assert classify_content(_norm("键盘开箱"), rules, "wuwa") == "unboxing"
    # 分区专属规则只对对应分区生效
    assert classify_content(_norm("舰船强化指南"), rules, "blhx") == "review"
    assert classify_content(_norm("舰船强化指南"), rules, "wuwa") is None
    assert classify_content(_norm("声骸强化指南"), rules, "wuwa") == "review"
    assert classify_content(_norm("声骸强化指南"), rules, "blhx") is None


def _norm(title: str):
    from app.models.enums import Identity, Platform
    from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

    return NormalizedContent(
        platform=Platform.BILIBILI,
        content_type=ContentType.VIDEO,
        external_id="BVtest",
        author=NormalizedAuthor(external_id="1", name="x", identity=Identity.USER),
        title=title,
    )


async def test_official_events_isolated_by_community(session):
    """同标题、同类目的官方公告，因分区不同不合并；事件归属各自分区。"""
    await seed_defaults(session)
    # 官方身份走解析器的官方 mid 集合（鸣潮 1955897084 / 碧蓝航线 233114659）
    wuwa_a = _video("BVoff1", "周年庆版本延期发货公告", owner_mid=1955897084, owner="鸣潮")
    wuwa_b = _video("BVoff2", "周年庆版本延期发货公告（更新）", owner_mid=1955897084, owner="鸣潮")
    blhx_a = _video("BVblhx1", "周年庆版本延期发货公告", owner_mid=233114659, owner="碧蓝航线")
    await ingest_items(session, "bilibili_search", [wuwa_a, wuwa_b], community="wuwa")
    await ingest_items(session, "bilibili_search", [blhx_a], community="blhx")

    stats = await run_aggregation(session)
    assert stats.events_created == 2

    events = list((await session.execute(select(Event))).scalars())
    by_community = {}
    for event in events:
        by_community.setdefault(event.community, []).append(event)
    assert {len(v) for v in by_community.values()} == {1}
    assert set(by_community) == {"wuwa", "blhx"}
    # 鸣潮事件两条证据，碧蓝航线事件一条
    evidence_counts = dict(
        (await session.execute(
            select(Evidence.event_id, func.count(Evidence.id)).group_by(Evidence.event_id)
        )).all()
    )
    wuwa_event = by_community["wuwa"][0]
    blhx_event = by_community["blhx"][0]
    assert evidence_counts[wuwa_event.id] == 2
    assert evidence_counts[blhx_event.id] == 1


async def test_community_content_isolation_on_reingest(session):
    """同一内容被另一分区的目标再次采集时，community 保持首次入库值不翻转。"""
    await ingest_items(session, "bilibili_search", [_video("BVmix", "联动视频")], community="wuwa")
    await ingest_items(session, "bilibili_search", [_video("BVmix", "联动视频")], community="blhx")
    raw = await session.scalar(select(RawContent).where(RawContent.external_id == "BVmix"))
    assert raw.community == "wuwa"


async def test_public_contents_community_filter(client, session):
    await ingest_items(session, "bilibili_search", [_video("BVw1", "鸣潮视频")], community="wuwa")
    await ingest_items(session, "bilibili_search", [_video("BVb1", "碧蓝航线视频")], community="blhx")
    await session.commit()

    default = await client.get("/api/contents")
    assert default.json()["total"] == 1  # 默认只返回鸣潮分区
    assert default.json()["items"][0]["external_id"] == "BVw1"

    blhx = await client.get("/api/contents", params={"community": "blhx"})
    assert blhx.json()["total"] == 1
    assert blhx.json()["items"][0]["external_id"] == "BVb1"

    invalid = await client.get("/api/contents", params={"community": "other"})
    assert invalid.status_code == 422


async def test_admin_review_community_endpoint(client, session, admin_headers):
    """按分区批量重审：只清本分区判定并入队，非法分区 422。"""
    await ingest_items(session, "bilibili_search", [_video("BVw2", "鸣潮内容")], community="wuwa")
    await ingest_items(session, "bilibili_search", [_video("BVb2", "碧蓝航线内容")], community="blhx")
    from app.models import ContentReview, RawContent

    rows = {
        r.external_id: r
        for r in (await session.execute(select(RawContent))).scalars()
    }
    # 预置旧判定（模拟 wuwa 语境下的误判）
    session.add(ContentReview(raw_content_id=rows["BVb2"].id, relevant=False, model="test"))
    await session.commit()

    from app.api import main as api_main

    fake = api_main.app.state.queue
    resp = await client.post(
        "/api/admin/ai/review-community", params={"community": "blhx"}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json() == {"community": "blhx", "cleared": 1, "queued": 1}
    assert rows["BVb2"].id in fake.reviewed
    assert rows["BVw2"].id not in fake.reviewed
    left = await session.scalar(
        select(func.count())
        .select_from(ContentReview)
        .where(ContentReview.raw_content_id == rows["BVb2"].id)
    )
    assert left == 0

    # only_missing 增量模式：已有判定记录的不再入队（模拟 worker 已重审完成）
    fake.reviewed.clear()
    session.add(ContentReview(raw_content_id=rows["BVb2"].id, relevant=True, model="test"))
    await session.commit()
    resp = await client.post(
        "/api/admin/ai/review-community",
        params={"community": "blhx", "only_missing": True},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"community": "blhx", "cleared": 0, "queued": 0}
    assert fake.reviewed == []

    invalid = await client.post(
        "/api/admin/ai/review-community", params={"community": "xyz"}, headers=admin_headers
    )
    assert invalid.status_code == 422


async def test_admin_products_community_filter(client, session, admin_headers):
    from app.models import Product

    session.add_all(
        [
            Product(slug="p-wuwa", name="鸣潮商品", community="wuwa"),
            Product(slug="p-blhx", name="碧蓝商品", community="blhx"),
        ]
    )
    await session.commit()
    total = len((await client.get("/api/admin/products", headers=admin_headers)).json()["items"])
    resp = await client.get(
        "/api/admin/products", params={"community": "blhx"}, headers=admin_headers
    )
    items = resp.json()["items"]
    assert items and all(p["community"] == "blhx" for p in items)
    assert 0 < len(items) < total


async def test_product_signal_community_follows_product(session):
    """履约信号显式携带商品分区（同步时强制覆盖，不依赖 join 推断）。"""
    from app.models import ContentVersion, Event, Evidence, Product, ProductSignal, RawContent, Source
    from app.services.fulfillment import sync_product_signals

    product = Product(slug="p-sig", name="信号分区商品", community="blhx", watch_terms=["信号分区"])
    source = Source(platform="bilibili", external_id="u-sig", name="用户", identity="user", raw_json={})
    session.add_all([product, source])
    await session.flush()
    raw = RawContent(
        platform="bilibili",
        content_type="video",
        external_id="BVsig1",
        source_id=source.id,
        url="https://example.com/BVsig1",
        raw_json={},
        community="blhx",
    )
    session.add(raw)
    await session.flush()
    session.add(
        ContentVersion(raw_content_id=raw.id, version=1, title="信号分区商品开箱", text="已到货")
    )
    event = Event(
        title="信号分区商品开箱",
        category="unboxing",
        identity="user",
        is_active=True,
        product_id=product.id,
        community="blhx",
    )
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id, role="support"))
    await session.commit()

    created = await sync_product_signals(session, product.id)
    assert created >= 1
    signal = (
        await session.execute(select(ProductSignal).where(ProductSignal.product_id == product.id))
    ).scalars().first()
    assert signal.community == "blhx"


async def test_public_aggregate_community_all(client, session):
    """community=all：跨分区聚合返回两个分区内容，非法写路径不受影响。"""
    await ingest_items(session, "bilibili_search", [_video("BVw3", "鸣潮内容")], community="wuwa")
    await ingest_items(session, "bilibili_search", [_video("BVb3", "碧蓝航线内容")], community="blhx")
    await session.commit()

    resp = await client.get("/api/contents", params={"community": "all"})
    titles = [i["title"] for i in resp.json()["items"]]
    assert "鸣潮内容" in titles and "碧蓝航线内容" in titles
    assert resp.json()["total"] == 2

    events = await client.get("/api/events", params={"community": "all", "limit": 10})
    assert events.status_code == 200
    highlights = await client.get("/api/highlights", params={"community": "all"})
    assert highlights.status_code == 200

    # official-links 保持严格分区校验，不接受 all（其余列表端点均已支持聚合视角）
    assert (
        await client.get("/api/official-links", params={"community": "all"})
    ).status_code == 422


async def test_products_community_all(client, session):
    """商品列表支持 community=all：跨分区聚合返回全部分区商品。"""
    from app.models import Product

    session.add_all(
        [
            Product(slug="pa-wuwa", name="鸣潮商品B", community="wuwa"),
            Product(slug="pa-blhx", name="碧蓝商品B", community="blhx"),
        ]
    )
    await session.commit()
    resp = await client.get("/api/products", params={"community": "all"})
    names = {p["name"] for p in resp.json()["items"]}
    assert "鸣潮商品B" in names and "碧蓝商品B" in names


async def test_sources_community_all(client, session):
    """来源健康支持 community=all：跨分区返回全部来源。"""
    from app.models import CollectTarget

    session.add_all(
        [
            CollectTarget(platform="bilibili", target_type="keyword_search", target_value="甲", community="wuwa"),
            CollectTarget(platform="bilibili", target_type="keyword_search", target_value="乙", community="blhx"),
        ]
    )
    await session.commit()
    resp = await client.get("/api/sources", params={"community": "all"})
    names = {s["name"] for s in resp.json()["items"]}
    assert {"甲", "乙"} <= names
    single = await client.get("/api/sources", params={"community": "blhx"})
    assert {s["name"] for s in single.json()["items"]} == {"乙"}
