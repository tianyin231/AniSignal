"""周边监控的周期和频率：到货才结束，等待期低频，请求量不随搜索结果放大。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.collectors.bilibili import BilibiliCollector
from app.models import CollectTarget, Product, ProductSignal, ProductSignalEvidence, RawContent
from app.services import product_discovery as discovery
from app.services.collect import filter_relevant
from app.services.custom_policy import (
    claim_collection_budget,
    get_policy,
    record_collection_requests,
)
from app.services.fulfillment_automation import decide_signal_automation
from app.services.product_monitoring import (
    WAITING_INTERVAL_MINUTES,
    WATCH_INTERVAL_MINUTES,
    apply_monitor_interval,
    configure_product_monitor,
    delivery_confirmed_at,
    monitor_interval_minutes,
)
from app.collectors.base import CollectorDeferred
from app.services.settings import get_setting, set_setting
from tests.test_parsers_bilibili import VIDEO_RAW

NOW = utcnow()


def signal(kind, days_ago, *, actor="unknown", status="verified", source="a", acquisition="unknown"):
    return (
        SimpleNamespace(id=hash((kind, days_ago, source)), signal_type=kind, actor_type=actor, review_status=status,
                        occurred_at=NOW - timedelta(days=days_ago)),
        SimpleNamespace(independent_source_key=source, acquisition_type=acquisition),
    )


def test_a_long_presale_with_no_news_is_not_finished():
    rows = [signal("presale_open", 180, actor="official")]
    assert delivery_confirmed_at(rows, None, NOW) is None
    assert monitor_interval_minutes(rows, None, None, NOW) == WAITING_INTERVAL_MINUTES


def test_official_shipping_confirms_delivery():
    rows = [signal("presale_open", 200, actor="official"), signal("shipping_started", 3, actor="official")]
    assert delivery_confirmed_at(rows, None, NOW) == rows[1][0].occurred_at


def test_one_unboxing_is_not_enough_without_a_ship_date():
    rows = [signal("consumer_received", 3)]
    assert delivery_confirmed_at(rows, None, NOW) is None


def test_two_different_authors_confirm_delivery_at_the_second_report():
    rows = [signal("consumer_received", 9, source="a"), signal("consumer_received", 2, source="b")]
    assert delivery_confirmed_at(rows, None, NOW) == rows[1][0].occurred_at


def test_the_same_author_posting_twice_counts_once():
    rows = [signal("consumer_received", 9, source="a"), signal("consumer_received", 2, source="a")]
    assert delivery_confirmed_at(rows, None, NOW) is None


def test_one_unboxing_after_the_ship_date_confirms_delivery():
    rows = [signal("consumer_received", 1)]
    assert delivery_confirmed_at(rows, NOW - timedelta(days=5), NOW) == rows[0][0].occurred_at


def test_reports_are_not_believed_when_shipping_is_far_away():
    rows = [signal("consumer_received", 9, source="a"), signal("consumer_received", 2, source="b")]
    assert delivery_confirmed_at(rows, NOW + timedelta(days=200), NOW) is None


def test_a_new_official_round_resets_what_counts_as_delivered():
    rows = [
        signal("consumer_received", 30, source="a"), signal("consumer_received", 29, source="b"),
        signal("balance_open", 10, actor="official"),
    ]
    assert delivery_confirmed_at(rows, None, NOW) is None
    rows += [signal("consumer_received", 3, source="c"), signal("consumer_received", 2, source="d")]
    assert delivery_confirmed_at(rows, None, NOW) == rows[-1][0].occurred_at


def test_seeded_and_unverified_reports_do_not_count():
    rows = [
        signal("consumer_received", 5, source="a", acquisition="seeded"),
        signal("consumer_received", 4, source="b", actor="seeded"),
        signal("consumer_received", 3, source="c", status="candidate"),
        signal("consumer_received", 2, source="d"),
    ]
    assert delivery_confirmed_at(rows, None, NOW) is None


def test_interval_speeds_up_around_the_ship_date_and_after_first_signs():
    assert monitor_interval_minutes([], NOW + timedelta(days=90), None, NOW) == WAITING_INTERVAL_MINUTES
    assert monitor_interval_minutes([], NOW + timedelta(days=20), None, NOW) == WATCH_INTERVAL_MINUTES
    assert monitor_interval_minutes([], NOW - timedelta(days=20), None, NOW) == WATCH_INTERVAL_MINUTES
    assert monitor_interval_minutes([], NOW - timedelta(days=90), None, NOW) == WAITING_INTERVAL_MINUTES
    fresh = [signal("consumer_received", 1, status="candidate")]
    assert monitor_interval_minutes(fresh, None, None, NOW) == WATCH_INTERVAL_MINUTES
    stale = [signal("consumer_received", 40)]
    assert monitor_interval_minutes(stale, None, None, NOW) == WAITING_INTERVAL_MINUTES
    rejected = [signal("consumer_received", 1, status="rejected")]
    assert monitor_interval_minutes(rejected, None, None, NOW) == WAITING_INTERVAL_MINUTES


async def monitored_product(session, **fields):
    product = Product(slug=f"p{len(fields)}-{NOW.timestamp()}", name="菲比手办", watch_terms=["菲比手办"],
                      created_at=NOW - timedelta(days=100), monitor_started_at=NOW - timedelta(days=100), **fields)
    session.add(product)
    await session.flush()
    await configure_product_monitor(session, product, enabled=True, keyword="菲比 手办")
    await session.commit()
    return product, await session.get(CollectTarget, product.monitor_target_id)


async def add_signal(session, product, kind, occurred_at, *, actor="official"):
    raw = RawContent(platform="bilibili", content_type="dynamic", external_id=f"{kind}-{occurred_at}", url="x",
                     raw_json={}, collected_at=NOW, last_seen_at=NOW, content_hash="h")
    session.add(raw)
    await session.flush()
    row = ProductSignal(product_id=product.id, signal_type=kind, actor_type=actor, review_status="verified",
                        summary=kind, occurred_at=occurred_at)
    session.add(row)
    await session.flush()
    session.add(ProductSignalEvidence(signal_id=row.id, raw_content_id=raw.id, independent_source_key=f"k{row.id}"))
    await session.commit()


async def test_sweep_slows_a_waiting_product_without_ending_it(session):
    product, target = await monitored_product(session)
    result = await discovery.settle_product_monitors(session, now=NOW)
    assert result["retimed"] == 1 and result["archived"] == 0
    assert target.interval_minutes == WAITING_INTERVAL_MINUTES
    assert product.monitor_status == "watching" and target.enabled


async def test_delivery_ends_monitoring_two_weeks_after_confirmation(session):
    product, target = await monitored_product(session)
    await add_signal(session, product, "shipping_started", NOW - timedelta(days=13))
    assert (await discovery.settle_product_monitors(session, now=NOW))["archived"] == 0
    assert target.interval_minutes == WATCH_INTERVAL_MINUTES

    assert (await discovery.settle_product_monitors(session, now=NOW + timedelta(days=2)))["archived"] == 1
    assert product.monitor_status == "archived" and target.enabled is False


async def test_a_resumed_product_gets_its_own_observation_window(session):
    product, _ = await monitored_product(session)
    await add_signal(session, product, "shipping_started", NOW - timedelta(days=40))
    product.monitor_started_at = NOW - timedelta(days=1)
    await session.commit()
    assert (await discovery.settle_product_monitors(session, now=NOW))["archived"] == 0


async def test_manually_set_interval_is_left_alone(session):
    product, target = await monitored_product(session)
    target.interval_minutes = 120
    await session.commit()
    assert not await apply_monitor_interval(session, product, WAITING_INTERVAL_MINUTES, NOW)
    assert target.interval_minutes == 120


async def test_speeding_up_does_not_wait_out_the_old_cycle(session):
    product, target = await monitored_product(session)
    target.interval_minutes = WAITING_INTERVAL_MINUTES
    target.next_run_at = NOW + timedelta(hours=5)
    assert await apply_monitor_interval(session, product, WATCH_INTERVAL_MINUTES, NOW)
    assert target.next_run_at == NOW + timedelta(minutes=WATCH_INTERVAL_MINUTES)


async def run_search(monkeypatch, collector, results):
    calls = []

    async def listing(*args):
        calls.append(args)
        return results

    async def run(*args):
        calls.append(args)
        return dict(results[0], description="详情")

    monkeypatch.setattr(collector, "_run_nonempty", listing)
    monkeypatch.setattr(collector, "_run", run)
    return calls, await collector.search_videos("菲比 手办", 20)


async def test_filtered_results_do_not_cost_a_detail_request(monkeypatch):
    collector = BilibiliCollector()
    target = CollectTarget(community="wuwa", target_type="keyword_search", target_value="菲比 手办", tier="core", min_views=0)
    collector.search_by_date = True
    collector.detail_filter = lambda item: bool(filter_relevant([dict(item)], target, product_watch_terms=["菲比手办"])[0])
    results = [dict(VIDEO_RAW, bvid="BVkeep", title="菲比手办开箱"),
               *[dict(VIDEO_RAW, bvid=f"BV{index}", title="无关视频") for index in range(19)]]

    calls, items = await run_search(monkeypatch, collector, results)

    assert calls[0][-2:] == ("--order", "pubdate")
    assert [call[0] for call in calls[1:]] == ["video"]
    assert len(items) == 20


async def test_default_search_still_fetches_every_detail(monkeypatch):
    collector = BilibiliCollector()
    calls, _ = await run_search(monkeypatch, collector, [dict(VIDEO_RAW, bvid=f"BV{index}") for index in range(3)])
    assert "--order" not in calls[0]
    assert len(calls) == 4


def test_figure_lines_count_as_goods_words():
    assert "守岸人粘土人" in discovery.build_watch_terms(
        "守岸人粘土人", [], "手办", search_keyword="守岸人 粘土人", community="wuwa")


async def test_low_priority_targets_stop_at_seventy_percent_and_official_targets_continue(session):
    policy = await get_policy(session)
    await set_setting(session, "custom_feed_policy", {**policy, "platform_hourly_budget": 10})
    for _ in range(7):
        await claim_collection_budget(session, "bilibili", low_priority=True)
    with pytest.raises(CollectorDeferred):
        await claim_collection_budget(session, "bilibili", low_priority=True)
    await claim_collection_budget(session, "bilibili")


async def test_real_requests_are_counted_and_enforced(session):
    policy = await get_policy(session)
    await set_setting(session, "custom_feed_policy", {**policy, "platform_hourly_requests": 100})
    await claim_collection_budget(session, "bilibili")
    await record_collection_requests(session, "bilibili", 80)
    assert (await get_setting(session, "collector_budget_bilibili"))["requests"] == 80
    with pytest.raises(CollectorDeferred):
        await claim_collection_budget(session, "bilibili", low_priority=True)
    await claim_collection_budget(session, "bilibili")
    await record_collection_requests(session, "bilibili", 20)
    with pytest.raises(CollectorDeferred):
        await claim_collection_budget(session, "bilibili")


async def test_other_platforms_are_not_metered(session):
    await record_collection_requests(session, "youtube", 50)
    assert await get_setting(session, "collector_budget_bilibili") is None


def automation(title, text="开箱到货了", **extra):
    review = {"schema_version": 2, "content_version": 1, "signals": [{
        "product_id": 1, "signal_type": "consumer_received", "verdict": "supported", "confidence": 95,
        "quote": "开箱到货了", "reason": "实物到手", "acquisition_type": "unknown",
    }]}
    return decide_signal_automation(
        signal_type="consumer_received", product_id=1, actor_type="unknown", title=title, text=text,
        content_version=1, review_fulfillment=review, **extra)


def test_genuine_unboxing_is_verified():
    assert automation("菲比手办开箱")["review_status"] == "verified"


@pytest.mark.parametrize("title", ["JK菲比手办翻模开箱", "【祖国版】菲比手办开箱", "我收到菲比预定卡啦", "盗版菲比手办开箱"])
def test_bootlegs_and_preorder_cards_are_not_verified_delivery(title):
    assert automation(title)["review_status"] == "candidate"


def test_a_warning_about_bootlegs_in_the_description_does_not_count():
    assert automation("菲比手办开箱", text="开箱到货了，支持正版拒绝盗版")["review_status"] == "verified"


def test_receipt_long_before_the_ship_date_is_not_verified():
    early = automation("菲比手办开箱", occurred_at=NOW, expected_ship_at=NOW + timedelta(days=300))
    assert early["review_status"] == "candidate"
    near = automation("菲比手办开箱", occurred_at=NOW, expected_ship_at=NOW + timedelta(days=20))
    assert near["review_status"] == "verified"


async def test_duplicate_item_in_a_batch_does_not_lose_the_rest(session, monkeypatch):
    from app.services import ingest

    original = ingest._ingest_one
    calls = 0

    async def collide(session, normalized, *args):
        nonlocal calls
        calls += 1
        if calls == 1:
            session.add(RawContent(platform="bilibili", content_type="video", external_id="dup", url="x", raw_json={},
                                   collected_at=NOW, last_seen_at=NOW, content_hash="h"))
            session.add(RawContent(platform="bilibili", content_type="video", external_id="dup", url="x", raw_json={},
                                   collected_at=NOW, last_seen_at=NOW, content_hash="h"))
            await session.flush()
        return await original(session, normalized, *args)

    monkeypatch.setattr(ingest, "_ingest_one", collide)
    stats = await ingest.ingest_items(session, "bilibili_search", [
        dict(VIDEO_RAW, bvid="BVfirst", aid=1), dict(VIDEO_RAW, bvid="BVsecond", aid=2)])

    assert stats.items_new == 1 and len(stats.errors) == 1
    assert await session.scalar(select(RawContent).where(RawContent.external_id == "BVsecond")) is not None
