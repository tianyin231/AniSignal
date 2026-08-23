"""事件聚合引擎与分类单测。"""

from sqlalchemy import func, select

from app.models import (
    ClassificationRule,
    Event,
    EventRevision,
    Evidence,
    Product,
    RawContent,
)
from app.services.classify import classify_content, seed_defaults
from app.services.events import cluster_announcements, run_aggregation
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import VIDEO_RAW


async def _video(bvid: str, title: str, owner_mid: int = 100, owner: str = "用户") -> dict:
    return dict(VIDEO_RAW, bvid=bvid, title=title, owner_mid=owner_mid, owner_name=owner, description="")


async def _seed_rules(session):
    added = await seed_defaults(session)
    assert added > 0


async def test_classification_keyword_priority(session):
    await _seed_rules(session)
    rules = list((await session.execute(select(ClassificationRule))).scalars())
    # "开箱" 优先于 "到货"（priority 更小）
    c = classify_content(_make_norm("爱弥斯键盘到货开箱"), rules)
    assert c == "unboxing"
    # 未命中关键词的官方公告（notice 走 kuro 流程，这里直接验证兜底函数属性）
    c2 = classify_content(_make_norm("随便一个标题"), rules)
    assert c2 is None


def _make_norm(title: str):
    from app.models.enums import Identity, Platform
    from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

    return NormalizedContent(
        platform=Platform.BILIBILI,
        content_type=ContentType.VIDEO,
        external_id="BVtest",
        author=NormalizedAuthor(external_id="1", name="x", identity=Identity.USER),
        title=title,
    )


async def test_official_announcements_merged_into_event(session):
    await _seed_rules(session)
    # 两条同类官方延期公告（标题共享片段，72h 内）→ 一个事件两证据
    a = dict(VIDEO_RAW, bvid="BVoff1", title="爱弥斯联名键盘延期发货公告", owner_mid=1955897084, owner_name="鸣潮")
    b = dict(VIDEO_RAW, bvid="BVoff2", title="爱弥斯联名键盘延期公告（更新）", owner_mid=1955897084, owner_name="鸣潮")
    await ingest_items(session, "bilibili_search", [a, b])
    stats = await run_aggregation(session)
    await session.commit()

    events = list((await session.execute(select(Event))).scalars())
    delay_events = [e for e in events if e.category == "delay"]
    assert len(delay_events) == 1
    event = delay_events[0]
    assert event.identity == "official"
    assert event.confidence == "high"
    count = await session.scalar(
        select(func.count(Evidence.id)).where(Evidence.event_id == event.id)
    )
    assert count == 2
    assert stats.events_created >= 1


async def test_aggregation_idempotent(session):
    await _seed_rules(session)
    a = dict(VIDEO_RAW, bvid="BVidem", title="爱弥斯联名键盘延期发货公告", owner_mid=1955897084, owner_name="鸣潮")
    await ingest_items(session, "bilibili_search", [a])
    await run_aggregation(session)
    await session.commit()
    revisions_1 = await session.scalar(select(func.count(EventRevision.id)))
    stats2 = await run_aggregation(session)
    await session.commit()

    # 第二次运行：无新事件、无新证据、无新修订
    assert stats2.events_created == 0
    assert stats2.evidences_added == 0
    assert stats2.revisions_written == 0
    revisions_2 = await session.scalar(select(func.count(EventRevision.id)))
    assert revisions_2 == revisions_1


async def test_product_fulfillment_ladder(session):
    await _seed_rules(session)
    session.add(
        Product(
            slug="aymiss-keyboard",
            name="爱弥斯联名键盘",
            watch_terms=["爱弥斯"],
        )
    )
    await session.commit()

    # 送测开箱 1 条 + 已核验普通用户开箱 3 条
    seeded = dict(VIDEO_RAW, bvid="BVseed", title="爱弥斯键盘开箱：官方送测样品", owner_mid=500, owner_name="送测UP")
    users = [
        await _video("BVu1", "爱弥斯键盘开箱第一天", owner_mid=601, owner="用户甲"),
        await _video("BVu2", "爱弥斯键盘开箱，等了好久", owner_mid=602, owner="用户乙"),
        await _video("BVu3", "爱弥斯键盘终于开箱了！", owner_mid=603, owner="用户丙"),
    ]
    await ingest_items(session, "bilibili_search", [seeded, *users])
    from app.models import Source

    sources = list((await session.execute(select(Source))).scalars())
    for source in sources:
        if source.external_id == "500":
            source.identity = "seeded"
        elif source.external_id in {"601", "602", "603"}:
            source.identity = "user"
    await run_aggregation(session)
    await session.commit()

    product = await session.scalar(select(Product))
    # 3 个已核验独立用户账号 → 阶段 3（多名独立用户到货）
    assert product.computed_stage == 3

    unboxing_events = list(
        (await session.execute(select(Event).where(Event.category == "unboxing"))).scalars()
    )
    assert len(unboxing_events) == 1
    event = unboxing_events[0]
    assert event.product_id == product.id
    assert event.status_label == "多名独立用户到货"
    assert event.importance is True
    assert "3 个独立账号" in event.conclusion or "独立账号" in event.conclusion


async def test_manual_locked_event_skipped(session):
    await _seed_rules(session)
    await ingest_items(
        session,
        "bilibili_search",
        [dict(VIDEO_RAW, bvid="BVlock", title="爱弥斯键盘延期公告", owner_mid=1955897084, owner_name="鸣潮")],
    )
    await run_aggregation(session)
    await session.commit()

    event = await session.scalar(select(Event))
    event.manual_locked = True
    await session.commit()

    stats = await run_aggregation(session)  # 再跑：证据已全，走 locked 分支或无变化
    await session.commit()
    assert stats.revisions_written == 0
    events = (await session.execute(select(Event))).scalars().all()
    assert len(events) == 1


async def test_unverified_accounts_do_not_advance_stage(session):
    await _seed_rules(session)
    product = Product(slug="p2", name="测试商品", watch_terms=["测试商品"], stage_hint=5)
    session.add(product)
    await session.commit()
    await ingest_items(
        session, "bilibili_search", [await _video("BVhint", "测试商品到货了")]
    )
    await run_aggregation(session)
    await session.commit()
    await session.refresh(product)
    # 未经身份核验的账号不能自动推进；人工展示值也不污染引擎计算值。
    assert product.computed_stage == 0


def test_cluster_shared_title_logic():
    from app.services.events import ContentRow

    def row(title: str, ts) -> ContentRow:
        return ContentRow(
            raw_id=1, platform="kuro_cn", external_id="x", url=None,
            published_at=ts, collected_at=ts, title=title, text="", category="delay",
            identity="official", source_name=None, source_external_id=None,
        )

    from datetime import datetime, timezone

    t0 = datetime(2026, 8, 20, tzinfo=timezone.utc).replace(tzinfo=None)
    a = row("爱弥斯联名键盘延期公告", t0)
    b = row("爱弥斯联名键盘延期发货说明", t0.replace(hour=10))
    c = row("完全无关的另一个公告标题", t0.replace(hour=12))
    clusters = cluster_announcements([a, b, c])
    assert len(clusters) == 2
    assert len(clusters[0]) == 2  # a b 同主题
    assert len(clusters[1]) == 1  # c 独立


def test_game_name_boilerplate_does_not_merge_topics():
    """共享“《鸣潮》”不代表同一事件主题。"""
    from datetime import datetime

    from app.services.events import ContentRow

    ts = datetime(2026, 8, 20, 8)

    def row(raw_id: int, title: str) -> ContentRow:
        return ContentRow(
            raw_id=raw_id,
            platform="kuro_cn",
            external_id=str(raw_id),
            url=None,
            published_at=ts,
            collected_at=ts,
            title=title,
            text="",
            category="official_announce",
            identity="official",
            source_name="鸣潮官网",
            source_external_id=None,
        )

    clusters = cluster_announcements(
        [
            row(1, "《鸣潮》防沉迷系统说明"),
            row(2, "《鸣潮》公平运营声明"),
        ]
    )
    assert len(clusters) == 2


def test_series_name_does_not_merge_different_topics():
    """“先约电台”既是活动名也是音乐栏目名，不能单独作为事件指纹。"""
    from datetime import datetime

    from app.services.events import ContentRow

    ts = datetime(2026, 8, 20, 8)

    def row(raw_id: int, title: str) -> ContentRow:
        return ContentRow(
            raw_id=raw_id,
            platform="kuro_cn" if raw_id == 1 else "bilibili",
            external_id=str(raw_id),
            url=None,
            published_at=ts,
            collected_at=ts,
            title=title,
            text="",
            category="version_update",
            identity="official",
            source_name="鸣潮",
            source_external_id=None,
        )

    clusters = cluster_announcements(
        [
            row(1, "[先约电台]活动说明"),
            row(2, "《鸣潮》先约电台EP3.6——清宵《尘外客》"),
        ]
    )
    assert len(clusters) == 2


async def test_different_topics_not_merged(session):
    """同类目但标题无共享片段的两条官方公告 → 各自成事件（修复回归）。"""
    await _seed_rules(session)
    await ingest_items(
        session,
        "kuro_notices",
        [
            {"platform": "kuro_cn", "entry": {"id": "7001", "tabTitle": {"zh-Hans": "防沉迷系统机制说明"}}, "detail": None},
            {"platform": "kuro_cn", "entry": {"id": "7002", "tabTitle": {"zh-Hans": "问题反馈渠道调整公告"}}, "detail": None},
        ],
    )
    await run_aggregation(session)
    await session.commit()
    announces = list(
        (await session.execute(select(Event).where(Event.category == "official_announce"))).scalars()
    )
    assert len(announces) == 2, "不同主题的公告不应合并"


async def test_official_recompute_reconciles_stale_evidence(session):
    """规则变化或旧聚合误判后，重算应精确对账而不是只追加证据。"""
    await _seed_rules(session)
    await ingest_items(
        session,
        "kuro_notices",
        [
            {"platform": "kuro_cn", "entry": {"id": "7101", "tabTitle": {"zh-Hans": "《鸣潮》防沉迷系统说明"}}, "detail": None},
            {"platform": "kuro_cn", "entry": {"id": "7102", "tabTitle": {"zh-Hans": "《鸣潮》公平运营声明"}}, "detail": None},
        ],
    )
    raw_ids = list(await session.scalars(select(RawContent.id).order_by(RawContent.id)))
    stale = Event(
        title="《鸣潮》防沉迷系统说明",
        category="official_announce",
        identity="official",
        confidence="high",
        regions=["cn"],
    )
    session.add(stale)
    await session.flush()
    session.add_all([Evidence(event_id=stale.id, raw_content_id=raw_id) for raw_id in raw_ids])
    await session.commit()

    stats = await run_aggregation(session)
    await session.commit()

    active = list(
        await session.scalars(
            select(Event).where(Event.category == "official_announce", Event.is_active.is_(True))
        )
    )
    assert len(active) == 2
    evidence_sets = []
    for event in active:
        evidence_sets.append(
            set(await session.scalars(select(Evidence.raw_content_id).where(Evidence.event_id == event.id)))
        )
    assert sorted(len(ids) for ids in evidence_sets) == [1, 1]
    assert set().union(*evidence_sets) == set(raw_ids)
    assert stats.evidences_removed == 1

    second = await run_aggregation(session)
    await session.commit()
    assert second.events_created == 0
    assert second.evidences_added == 0
    assert second.evidences_removed == 0
    assert second.revisions_written == 0
