"""商品档案与每日情报简报（批次 3b/3c）单测。

覆盖：
- 3b 价格抽取（保守策略，版本号/期数不误报）、曲线采样与到价提醒触发边界；
- 3b 用户手动状态标记与到价提醒 CRUD、AI 差评聚合（未配置 AI 时安全回退）；
- 3b 发售日历分组、档案聚合视图；
- 3c 简报分节采集、AI 摘要的数字事实校验与回退、推送去重；
- 两端管理端与账号中心接口，以及未登录/未授权边界。
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import (
    BotTarget,
    DailyBrief,
    Event,
    Evidence,
    NotificationDelivery,
    Product,
    ProductFeedback,
    ProductPricePoint,
    ProductTrackState,
    RawContent,
    User,
    WebhookConfig,
)
from app.models.content import ContentVersion
from app.services import briefing, product_dossier
from tests.test_account_api import _register_and_login

# ---------- 工具 ----------


async def _content(
    session,
    *,
    external_id: str,
    title: str = "周边开订",
    text: str = "",
    keywords: list[str] | None = None,
    platform: str = "bilibili",
    community: str = "wuwa",
    intent_tier: str | None = None,
    collected_at: datetime | None = None,
    published_at: datetime | None = None,
) -> RawContent:
    raw = RawContent(
        platform=platform,
        content_type="video",
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        published_at=published_at or (utcnow() - timedelta(hours=2)),
        collected_at=collected_at or utcnow(),
        raw_json={},
        content_hash=f"hash-{external_id}",
        matched_keywords=keywords or [],
        community=community,
        intent_tier=intent_tier,
    )
    session.add(raw)
    await session.flush()
    session.add(
        ContentVersion(
            raw_content_id=raw.id,
            version=1,
            title=title,
            text=text,
            category="merch",
            parser_version="1",
        )
    )
    await session.flush()
    await session.commit()
    return raw


async def _product(session, *, slug: str = "wuwa-figure", name: str = "鸣潮 手办") -> Product:
    product = Product(
        slug=slug,
        name=name,
        community="wuwa",
        publisher="库洛",
        skus=["标准版"],
        price_note="待定",
        watch_terms=["鸣潮 手办"],
    )
    session.add(product)
    await session.flush()
    await session.commit()
    return product


async def _user(session, *, email: str = "u@t.com", notify: bool = True) -> User:
    user = User(
        email=email,
        password_hash="scrypt$1$1$1$aa$bb",
        is_verified=True,
        notification_enabled=notify,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


def _ai_config(**overrides):
    """桩一个已配置的 AI 通道（get_ai_config 是协程函数，桩也必须是）。"""
    config = {
        "base_url": "http://x",
        "api_key": "k",
        "model": "m",
        "price_in": 0,
        "price_out": 0,
    }
    config.update(overrides)

    async def _get(session):
        return config

    return _get


# ---------- 价格抽取 ----------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("定价 199 元，今日开订", 199.0),
        ("售价：1299", 1299.0),
        ("到手价 89.5", 89.5),
        ("RMB 459", 459.0),
        ("¥39 起", 39.0),
        ("支持 CNY 259", 259.0),
        # 保守：版本号、期数、时长一类不当成价格
        ("版本 v2.3 上线", None),
        ("活动第 3 期开启", None),
        ("第 2 阶段奖励", None),
        ("本次没有价格信息", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_price_is_conservative(text, expected):
    assert product_dossier.extract_price(text) == expected


def test_extract_price_takes_the_lowest_plausible_value():
    # 多个候选时取最低：到手价通常比原价更接近用户关心的「现在多少钱」
    assert product_dossier.extract_price("原价 599，到手价 399") == 399.0


def test_extract_price_rejects_absurd_numbers():
    # 数字被上下文里的更长数字截断时，匹配到的只是尾巴，不当成价格
    assert product_dossier.extract_price("编号 123456 元") is None
    assert product_dossier.extract_price("订单号 987654321，售价见详情") is None
    assert product_dossier.extract_price("¥1234567") is None


# ---------- 曲线与热度 ----------


async def test_record_price_point_updates_profile_and_series(session):
    product = await _product(session)

    await product_dossier.record_price_point(
        session, product.id, price=299.0, heat=12.5, hits=4, source="manual"
    )
    await product_dossier.record_price_point(session, product.id, price=259.0, heat=9.0, hits=3)

    profile = await product_dossier.get_profile(session, product.id)
    assert profile.price_current == 259.0  # 档案头跟随最后一次观测
    assert profile.price_observed_at is not None
    assert profile.price_source == "manual"

    points = await product_dossier.series(session, product.id, days=30)
    assert len(points) == 1  # 同一天聚合成一个点，取最后一次观测
    assert points[0]["price"] == 259.0
    assert points[0]["heat"] == 9.0
    assert points[0]["hits"] == 4  # hits 取当日最大
    assert points[0]["date"] == utcnow().date().isoformat()


async def test_series_respects_day_window(session):
    product = await _product(session)
    old = utcnow() - timedelta(days=40)
    session.add(ProductPricePoint(product_id=product.id, captured_at=old, price=99.0))
    await session.commit()
    assert len(await product_dossier.series(session, product.id, days=7)) == 0
    assert len(await product_dossier.series(session, product.id, days=90)) == 1

    # 今天的采样只在窗口内出现一次
    await product_dossier.record_price_point(session, product.id, price=100.0)
    assert len(await product_dossier.series(session, product.id, days=7)) == 1
    assert len(await product_dossier.series(session, product.id, days=90)) == 2


async def test_heat_score_counts_daily_hits(session):
    product = await _product(session)
    today = utcnow()
    for index in range(3):
        await _content(
            session,
            external_id=f"today-{index}",
            title="鸣潮 手办 开订",
            text="定价 199 元",
            keywords=["鸣潮 手办"],
            collected_at=today,
        )
    for index in range(2):
        await _content(
            session,
            external_id=f"yesterday-{index}",
            title="鸣潮 手办 预告",
            keywords=["鸣潮 手办"],
            collected_at=today - timedelta(days=1),
        )
    # 3（当日）+ 2/6（近 7 日日均）
    assert await product_dossier.heat_score(session, product, today) == round(3 + 2 / 6, 2)


async def test_heat_score_buckets_a_full_week(session):
    """热度分按 UTC 天分桶：一次分组查询要覆盖 7 天，桶不能错位（PERF-015）。"""
    product = await _product(session)
    now = utcnow()
    plan = {0: 1, 1: 2, 2: 3, 3: 3, 4: 3, 5: 3, 6: 3}
    for offset, count in plan.items():
        for index in range(count):
            await _content(
                session,
                external_id=f"week-{offset}-{index}",
                title="鸣潮 手办 开订",
                keywords=["鸣潮 手办"],
                collected_at=now - timedelta(days=offset),
            )
    # 当日 1 + 其余 6 天日均 (2+3×5)/6
    assert await product_dossier.heat_score(session, product, now) == round(1 + 17 / 6, 2)
    # daily_hits 仍按单天取数
    assert await product_dossier.daily_hits(session, product, now.date()) == 1
    assert await product_dossier.daily_hits(session, product, (now - timedelta(days=2)).date()) == 3
    # 没有监控词的商品：零成本返回
    other = await _product(session, slug="wuwa-plush", name="玩偶")
    other.watch_terms = []
    await session.commit()
    assert await product_dossier.heat_score(session, other, now) == 0.0


async def test_heat_score_ignores_hidden_and_other_community(session):
    product = await _product(session)
    await _content(
        session, external_id="hidden", title="鸣潮 手办", keywords=["鸣潮 手办"]
    )
    await _content(
        session,
        external_id="other-community",
        title="鸣潮 手办",
        keywords=["鸣潮 手办"],
        community="hsr",
    )
    hidden = await session.scalar(select(RawContent).where(RawContent.external_id == "hidden"))
    hidden.hidden = True
    await session.commit()
    assert await product_dossier.heat_score(session, product) == 0.0


# ---------- 价格采样扫描 ----------


async def test_sweep_price_points_extracts_and_lowers(session):
    product = await _product(session)
    await _content(
        session,
        external_id="price-1",
        title="鸣潮 手办 开订",
        text="售价：399",
        keywords=["鸣潮 手办"],
    )
    result = await product_dossier.sweep_price_points(session)
    assert result["scanned"] == 1
    assert result["points"] == 1
    assert result["priced"] == 1  # 首次观测到价格

    profile = await product_dossier.get_profile(session, product.id)
    assert profile.price_current == 399.0

    # 第二次扫到更低价才记新点；同价不重复刷曲线
    await _content(
        session,
        external_id="price-2",
        title="鸣潮 手办 到手价",
        text="到手价 299",
        keywords=["鸣潮 手办"],
    )
    result = await product_dossier.sweep_price_points(session)
    assert result["points"] == 1
    assert result["lowered"] == 1
    assert (await product_dossier.get_profile(session, product.id)).price_current == 299.0

    # 只剩同价/更贵内容时不再记点
    await _content(
        session,
        external_id="price-3",
        title="鸣潮 手办 原价",
        text="定价 499",
        keywords=["鸣潮 手办"],
    )
    assert (await product_dossier.sweep_price_points(session))["points"] == 0
    # 两次观测落在同一天，曲线按天聚合成一个点，取当日最后一次观测
    points = await product_dossier.series(session, product.id)
    assert len(points) == 1
    assert points[0]["price"] == 299.0


async def test_sweep_price_points_skips_products_without_terms(session):
    product = await _product(session, slug="no-terms", name="无监控词商品")
    product.watch_terms = []
    await session.commit()
    await _content(session, external_id="x", title="随便", text="定价 99")
    assert (await product_dossier.sweep_price_points(session))["points"] == 0


# ---------- AI 差评聚合 ----------


async def test_aggregate_feedback_falls_back_without_ai(session, monkeypatch):
    product = await _product(session)
    await _content(
        session,
        external_id="fb-1",
        title="鸣潮 手办翻车了",
        text="这款鸣潮 手办涂装翻车，边缘毛刺明显，不太值。",
        keywords=["鸣潮 手办"],
    )
    stats = await product_dossier.aggregate_feedback(session, product)
    assert stats["created"] == 1
    assert stats["ai"] is False  # 未配置 AI

    rows = await product_dossier.list_feedback(session, product.id)
    assert len(rows) == 1
    assert rows[0]["sentiment"] == "negative"
    assert rows[0]["source_method"] == "fallback"
    assert rows[0]["content_id"] is not None
    assert "涂装" in rows[0]["summary"]

    # 幂等：重复跑只更新，不新增
    stats = await product_dossier.aggregate_feedback(session, product)
    assert stats["created"] == 0
    assert stats["updated"] == 1
    assert len(await product_dossier.list_feedback(session, product.id)) == 1


async def test_aggregate_feedback_uses_ai_when_configured(session, monkeypatch):
    product = await _product(session)
    await _content(
        session,
        external_id="fb-ai",
        title="鸣潮 手办质量反馈",
        text="这只鸣潮 手办涂装有点糙，但造型还行。",
        keywords=["鸣潮 手办"],
    )

    async def _fake_chat(config, system, user, *, json_mode=False):
        return (
            '{"sentiment": "mixed", "summary": "涂装被吐槽但造型获认可", "quote": "涂装有点糙"}',
            {"prompt_tokens": 10, "completion_tokens": 5},
        )

    monkeypatch.setattr("app.services.ai.chat", _fake_chat)
    monkeypatch.setattr("app.services.ai.get_ai_config", _ai_config())
    monkeypatch.setattr("app.services.ai.ai_available", lambda config: True)

    stats = await product_dossier.aggregate_feedback(session, product)
    assert stats["ai"] is True
    assert stats["created"] == 1
    rows = await product_dossier.list_feedback(session, product.id)
    assert rows[0]["sentiment"] == "mixed"
    assert rows[0]["source_method"] == "ai"
    assert rows[0]["score"] == 0.8


async def test_aggregate_feedback_ai_failure_falls_back(session, monkeypatch):
    product = await _product(session)
    await _content(
        session,
        external_id="fb-fail",
        title="鸣潮 手办差评",
        text="这只鸣潮 手办做工不行",
        keywords=["鸣潮 手办"],
    )

    async def _boom(config, system, user, *, json_mode=False):
        raise RuntimeError("model down")

    monkeypatch.setattr("app.services.ai.chat", _boom)
    monkeypatch.setattr("app.services.ai.get_ai_config", _ai_config())
    monkeypatch.setattr("app.services.ai.ai_available", lambda config: True)

    stats = await product_dossier.aggregate_feedback(session, product)
    assert stats["created"] == 1
    rows = await product_dossier.list_feedback(session, product.id)
    assert rows[0]["source_method"] == "fallback"  # 失败不阻塞，回退原文片段


async def test_set_feedback_reviewed(session):
    product = await _product(session)
    session.add(
        ProductFeedback(
            product_id=product.id, content_id=None, summary="涂装瑕疵", sentiment="negative"
        )
    )
    await session.commit()
    row = await product_dossier.set_feedback_reviewed(session, 1, True)
    assert row is not None and row.reviewed is True
    assert await product_dossier.set_feedback_reviewed(session, 999, True) is None


# ---------- 用户状态与到价提醒 ----------


async def test_track_state_upsert_and_clear(session):
    product = await _product(session)
    user = await _user(session)
    row = await product_dossier.set_track_state(session, user.id, product.id, "ordered", "天猫")
    assert row.state == "ordered"
    assert row.note == "天猫"

    row = await product_dossier.set_track_state(session, user.id, product.id, "arrived")
    assert row.state == "arrived" and row.note is None  # 未传 note 时清空
    same = await session.scalar(select(ProductTrackState))
    assert same.id == row.id  # 同一行，不是新增

    assert await product_dossier.clear_track_state(session, user.id, product.id) is True
    assert await product_dossier.clear_track_state(session, user.id, product.id) is False


async def test_track_state_rejects_unknown_state(session):
    product = await _product(session)
    user = await _user(session)
    row = await product_dossier.set_track_state(session, user.id, product.id, "不存在")
    assert row.state == "watching"  # 归一化到默认值，不写脏数据


async def test_price_alert_rejects_bad_price(session):
    product = await _product(session)
    user = await _user(session)
    for bad in (0, -5, "abc", None):
        with pytest.raises(ValueError):
            await product_dossier.set_price_alert(session, user.id, product.id, bad)


async def test_price_alert_reset_allows_refire(session):
    product = await _product(session)
    user = await _user(session)
    row = await product_dossier.set_price_alert(session, user.id, product.id, 300)
    row.triggered_at = utcnow()
    row.triggered_price = 280.0
    row.trigger_count = 1
    await session.commit()

    row = await product_dossier.set_price_alert(session, user.id, product.id, 250)
    assert row.target_price == 250
    assert row.triggered_at is None and row.trigger_count == 0
    assert await product_dossier.clear_price_alert(session, user.id, product.id) is True


async def test_list_tracker_joins_profile_and_alert(session):
    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.price_current = 268.0
    profile.cover_url = "https://img/1.jpg"
    await session.commit()
    user = await _user(session)
    await product_dossier.set_track_state(session, user.id, product.id, "ordered")
    await product_dossier.set_price_alert(session, user.id, product.id, 300)

    items = await product_dossier.list_tracker(session, user.id)
    assert len(items) == 1
    item = items[0]
    assert item["slug"] == "wuwa-figure"
    assert item["state_label"] == "已预订"
    assert item["price_current"] == 268.0
    assert item["alert"]["target_price"] == 300
    assert item["alert"]["enabled"] is True


# ---------- 到价提醒扫描 ----------


async def test_sweep_price_alerts_triggers_and_dedupes(session):
    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.price_current = 199.0
    await session.commit()
    user = await _user(session)
    alert = await product_dossier.set_price_alert(session, user.id, product.id, 250)

    result = await product_dossier.sweep_price_alerts(session)
    assert result["checked"] == 1
    assert result["triggered"] == 1
    await session.refresh(alert)
    assert alert.trigger_count == 1
    assert alert.triggered_price == 199.0

    # 同一天再扫：仍然触发（每轮一次），trigger_count 递增且投递按计数去重
    result = await product_dossier.sweep_price_alerts(session)
    assert result["triggered"] == 1
    await session.refresh(alert)
    assert alert.trigger_count == 2


async def test_sweep_price_alerts_skips_when_price_above_target(session):
    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.price_current = 499.0
    await session.commit()
    user = await _user(session)
    await product_dossier.set_price_alert(session, user.id, product.id, 250)

    result = await product_dossier.sweep_price_alerts(session)
    assert result["triggered"] == 0
    assert result["skipped"] == 1


async def test_sweep_price_alerts_skips_without_price(session):
    product = await _product(session)
    await product_dossier.ensure_profile(session, product.id)
    user = await _user(session)
    await product_dossier.set_price_alert(session, user.id, product.id, 250)
    assert (await product_dossier.sweep_price_alerts(session))["triggered"] == 0


async def test_sweep_price_alerts_respects_disabled(session):
    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.price_current = 99.0
    await session.commit()
    user = await _user(session)
    alert = await product_dossier.set_price_alert(session, user.id, product.id, 250)
    alert.enabled = False
    await session.commit()
    assert (await product_dossier.sweep_price_alerts(session))["triggered"] == 0


async def test_price_alert_notifies_qq_and_email_channels(session):
    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.price_current = 188.0
    await session.commit()
    user = await _user(session)
    target = BotTarget(name="群", umo="qq:1", enabled=True, config={})
    session.add(target)
    await session.flush()
    await product_dossier.set_price_alert(session, user.id, product.id, 200)

    result = await product_dossier.sweep_price_alerts(session)
    assert result["triggered"] == 1
    # QQ 投递 + OnePush/邮件按配置情况，至少机器人这条落地
    deliveries = list(
        (await session.execute(select(BotTarget))).scalars()
    )
    assert deliveries and deliveries[0].id == target.id
    from app.models import BotDelivery

    rows = list((await session.execute(select(BotDelivery))).scalars())
    assert len(rows) == 1
    assert rows[0].kind == "critical"
    assert "到价提醒" in rows[0].body
    assert rows[0].dedupe_key == f"price-alert:{1}:{1}"


# ---------- 发售日历 ----------


async def test_calendar_groups_by_release_time(session):
    now = utcnow()
    upcoming_product = await _product(session, slug="soon", name="即将发售")
    ongoing_product = await _product(session, slug="now", name="已发售")
    ended_product = await _product(session, slug="done", name="已发货")
    no_time_product = await _product(session, slug="nodate", name="没有时间")

    for product, release_at, state in (
        (upcoming_product, now + timedelta(days=30), "announced"),
        (ongoing_product, now - timedelta(days=5), "on_sale"),
        (ended_product, now - timedelta(days=60), "shipped"),
        (no_time_product, None, "unknown"),
    ):
        profile = await product_dossier.ensure_profile(session, product.id)
        profile.release_at = release_at
        profile.release_state = state
    await session.commit()

    body = await product_dossier.calendar(session, now)
    slugs = lambda key: [item["slug"] for item in body[key]]  # noqa: E731
    assert slugs("upcoming") == ["soon"]
    assert slugs("ongoing") == ["now"]
    assert slugs("ended") == ["done"]
    assert "nodate" not in slugs("upcoming") + slugs("ongoing") + slugs("ended")


async def test_calendar_infers_state_when_unset(session):
    now = utcnow()
    product = await _product(session, slug="infer", name="待推断")
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.release_at = now - timedelta(days=2)
    profile.release_state = "unknown"
    await session.commit()
    body = await product_dossier.calendar(session, now)
    assert body["ongoing"][0]["release_state"] == "on_sale"
    assert body["ongoing"][0]["release_state_label"] == "已发售"


async def test_calendar_falls_back_to_presale_window(session):
    now = utcnow()
    product = await _product(session, slug="presale", name="仅预售窗口")
    product.presale_at = (now + timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    await session.commit()
    body = await product_dossier.calendar(session, now)
    assert [item["slug"] for item in body["upcoming"]] == ["presale"]
    assert body["upcoming"][0]["presale_at"] is not None


# ---------- 档案聚合视图 ----------


async def test_dossier_assembles_header_timeline_series_feedback(session):
    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.cover_url = "https://img/cover.jpg"
    profile.official_url = "https://official.example"
    profile.price_current = 259.0
    profile.release_at = utcnow() + timedelta(days=45)
    profile.release_state = "announced"
    await product_dossier.record_price_point(session, product.id, price=259.0, heat=5.0)
    session.add(
        ProductFeedback(product_id=product.id, summary="掉漆", sentiment="negative", score=0.9)
    )
    event = Event(
        title="鸣潮 手办 二期公布",
        community="wuwa",
        category="merch",
        product_id=product.id,
        importance=True,
    )
    session.add(event)
    await session.flush()
    raw = await _content(
        session, external_id="ev-1", title="鸣潮 手办 二期公布", keywords=["鸣潮 手办"]
    )
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id, role="support"))
    await session.commit()

    payload = await product_dossier.dossier(session, product.id)
    assert payload["name"] == "鸣潮 手办"
    assert payload["profile"]["cover_url"] == "https://img/cover.jpg"
    assert payload["profile"]["price_current"] == 259.0
    assert len(payload["series"]) == 1
    assert len(payload["feedback"]) == 1
    assert payload["feedback"][0]["sentiment_label"] == "差评"
    assert len(payload["timeline"]) == 1
    assert payload["timeline"][0]["evidence_count"] == 1
    assert payload["timeline"][0]["importance"] is True
    assert payload["calendar_entry"]["slug"] == "wuwa-figure"
    assert payload["fulfillment"]["stage_label"]


async def test_dossier_returns_empty_for_missing_product(session):
    assert await product_dossier.dossier(session, 9999) == {}


async def test_refresh_heat_updates_all_products(session):
    product = await _product(session)
    await _content(
        session, external_id="heat-1", title="鸣潮 手办", keywords=["鸣潮 手办"]
    )
    result = await product_dossier.refresh_heat(session)
    assert result["updated"] == 1
    assert (await product_dossier.get_profile(session, product.id)).heat_score == 1.0


# ---------- 3c：简报分节采集 ----------


async def test_collect_sections_gathers_all_four(session):
    product = await _product(session)
    now = utcnow()
    yesterday = now - timedelta(days=1)
    raw = await _content(
        session,
        external_id="hit-1",
        title="鸣潮 手办 开订",
        text="定价 199",
        keywords=["鸣潮 手办"],
        intent_tier="important",
        collected_at=yesterday,
        published_at=yesterday,
    )
    event = Event(
        title="鸣潮 手办 开订公告",
        community="wuwa",
        category="merch",
        product_id=product.id,
        importance=True,
        first_seen_at=yesterday,
    )
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id, role="support"))
    await session.commit()

    sections = await briefing.collect_sections(session, yesterday.date())
    assert len(sections["events"]) == 1
    assert sections["events"][0]["title"] == "鸣潮 手办 开订公告"
    assert sections["events"][0]["evidence_count"] == 1
    assert len(sections["top_hits"]) == 1
    assert sections["top_hits"][0]["intent_reason"] is None  # 未打理由时为 None
    assert len(sections["products"]) == 1
    assert sections["products"][0]["name"] == "鸣潮 手办"
    assert sections["products"][0]["new_evidence"] == 1
    assert sections["volume"]["total"] == 1
    assert sections["volume"]["top_keywords"][0]["keyword"] == "鸣潮 手办"


async def test_collect_sections_excludes_hidden_and_ignored(session):
    now = utcnow()
    yesterday = now - timedelta(days=1)
    visible = await _content(
        session,
        external_id="visible",
        title="正常命中",
        intent_tier="important",
        collected_at=yesterday,
    )
    hidden = await _content(
        session, external_id="hidden", title="隐藏命中", intent_tier="important", collected_at=yesterday
    )
    ignored = await _content(
        session, external_id="ignored", title="已忽略", intent_tier="ignore", collected_at=yesterday
    )
    hidden.hidden = True
    await session.commit()

    sections = await briefing.collect_sections(session, yesterday.date())
    ids = [item["content_id"] for item in sections["top_hits"]]
    assert ids == [visible.id]
    assert sections["volume"]["total"] == 1


async def test_collect_sections_empty_day_is_safe(session):
    sections = await briefing.collect_sections(session, date(2020, 1, 1))
    assert sections == {
        "events": [],
        "top_hits": [],
        "products": [],
        "volume": {"total": 0, "by_platform": {}, "top_keywords": []},
    }


# ---------- 3c：AI 摘要与事实校验 ----------


def test_summary_supported_rejects_numbers_not_in_facts():
    facts = "昨日命中总数：12\n关键词 鸣潮：7 条"
    assert briefing._summary_supported("昨天一共 12 条命中", facts) is True
    assert briefing._summary_supported("昨天一共 99 条命中", facts) is False


async def test_compose_summary_falls_back_without_ai(session):
    sections = {
        "events": [],
        "top_hits": [],
        "products": [],
        "volume": {"total": 3, "by_platform": {"bilibili": 3}, "top_keywords": []},
    }
    summary, ai_status, model = await briefing._compose_summary(session, date(2026, 10, 17), sections)
    assert ai_status == "fallback"
    assert model is None
    assert "3 条命中" in summary


async def test_compose_summary_uses_ai_output(session, monkeypatch):
    sections = {
        "events": [{"title": "鸣潮 手办开订", "category": "merch", "importance": True,
                    "evidence_count": 2, "summary": None, "identity": "official"}],
        "top_hits": [],
        "products": [],
        "volume": {"total": 5, "by_platform": {"bilibili": 5},
                   "top_keywords": [{"keyword": "鸣潮 手办", "count": 5}]},
    }

    async def _fake_chat(config, system, user, *, json_mode=False):
        return ('{"summary": "昨日 5 条命中集中在鸣潮 手开订事件上"}', {"prompt_tokens": 1})

    monkeypatch.setattr("app.services.ai.chat", _fake_chat)
    monkeypatch.setattr("app.services.ai.get_ai_config", _ai_config())
    monkeypatch.setattr("app.services.ai.ai_available", lambda config: True)

    summary, ai_status, model = await briefing._compose_summary(session, date(2026, 10, 17), sections)
    assert ai_status == "ai"
    assert model == "m"
    assert "5 条命中" in summary


async def test_compose_summary_rejects_hallucinated_numbers(session, monkeypatch):
    sections = {
        "events": [],
        "top_hits": [],
        "products": [],
        "volume": {"total": 2, "by_platform": {}, "top_keywords": []},
    }

    async def _fake_chat(config, system, user, *, json_mode=False):
        return ('{"summary": "昨日爆发式新增 8888 条命中"}', None)

    monkeypatch.setattr("app.services.ai.chat", _fake_chat)
    monkeypatch.setattr("app.services.ai.get_ai_config", _ai_config())
    monkeypatch.setattr("app.services.ai.ai_available", lambda config: True)

    summary, ai_status, _ = await briefing._compose_summary(session, date(2026, 10, 17), sections)
    assert ai_status == "fallback"  # 编造数字 → 退回模板文
    assert "8888" not in summary


async def test_compose_summary_handles_ai_exception(session, monkeypatch):
    sections = {"events": [], "top_hits": [], "products": [], "volume": {"total": 0}}

    async def _boom(config, system, user, *, json_mode=False):
        raise RuntimeError("timeout")

    monkeypatch.setattr("app.services.ai.chat", _boom)
    monkeypatch.setattr("app.services.ai.get_ai_config", _ai_config())
    monkeypatch.setattr("app.services.ai.ai_available", lambda config: True)

    summary, ai_status, _ = await briefing._compose_summary(session, date(2026, 10, 17), sections)
    assert ai_status == "fallback"
    assert summary  # 简报永远能生成


# ---------- 3c：生成、渲染与推送 ----------


async def test_generate_brief_is_idempotent(session):
    now = utcnow()
    yesterday = now - timedelta(days=1)
    await _content(session, external_id="b1", title="鸣潮 手办", keywords=["鸣潮 手办"],
                   collected_at=yesterday, published_at=yesterday)

    brief, created = await briefing.generate_brief(session, yesterday.date())
    assert created is True
    assert brief.title.startswith("AniSignal 每日情报简报")
    assert brief.stats["volume_total"] == 1
    assert brief.status == "ready"

    again, created_again = await briefing.generate_brief(session, yesterday.date())
    assert created_again is False
    assert again.id == brief.id  # 同一天不重复生成

    forced, _ = await briefing.generate_brief(session, yesterday.date(), force=True)
    assert forced.id == brief.id
    assert await session.scalar(select(DailyBrief)) is not None


async def test_render_brief_text_has_all_sections(session):
    now = utcnow()
    yesterday = now - timedelta(days=1)
    product = await _product(session)
    raw = await _content(
        session, external_id="r1", title="鸣潮 手办 开订", keywords=["鸣潮 手办"],
        intent_tier="important", collected_at=yesterday, published_at=yesterday,
    )
    event = Event(
        title="鸣潮 手办 开订公告", community="wuwa", category="merch",
        product_id=product.id, importance=True, first_seen_at=yesterday,
    )
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id, role="support"))
    await session.commit()

    brief, _ = await briefing.generate_brief(session, yesterday.date())
    subject, body = briefing.render_brief_text(brief)
    assert subject == brief.title
    assert "▎声量回顾" in body
    assert "▎昨日事件汇总" in body
    assert "▎重要命中 Top" in body
    assert "▎商品进展" in body
    assert "鸣潮 手办 开订公告" in body
    assert "★" in body  # 重要事件带星标


async def test_deliver_brief_sends_email_and_qq(session):
    user = await _user(session)
    target = BotTarget(name="群", umo="qq:1", enabled=True, config={})
    session.add(target)
    await session.flush()
    brief, _ = await briefing.generate_brief(session, date(2026, 10, 17))

    result = await briefing.deliver_brief(session, brief)
    assert result["qq"] == 1
    assert result["status"] == "sent"
    assert brief.sent_at is not None

    from app.models import BotDelivery

    rows = list((await session.execute(select(BotDelivery))).scalars())
    assert len(rows) == 1 and rows[0].dedupe_key == f"brief:{brief.id}:{target.id}"
    # 邮件投递落库（SMTP 未配置时状态为 failed，但记录可查）
    deliveries = list((await session.execute(select(NotificationDelivery))).scalars())
    assert [d.user_id for d in deliveries] == [user.id]
    assert deliveries[0].kind == "brief"
    assert deliveries[0].dedupe_key == f"brief:{brief.id}:{user.id}"


async def test_deliver_brief_dedupes_per_user(session):
    await _user(session)
    target = BotTarget(name="群", umo="qq:2", enabled=True, config={})
    session.add(target)
    await session.flush()
    brief, _ = await briefing.generate_brief(session, date(2026, 10, 17))

    await briefing.deliver_brief(session, brief)
    await briefing.deliver_brief(session, brief)  # 重推

    from app.models import BotDelivery, NotificationDelivery

    assert len(list((await session.execute(select(NotificationDelivery))).scalars())) == 1
    # 机器人投递按简报去重（create_delivery 幂等）
    assert len(list((await session.execute(select(BotDelivery))).scalars())) == 1


async def test_deliver_brief_reaches_every_enabled_bot_target(session):
    """dedupe key 必须带 target.id：只按 brief.id 去重会挡住第一个目标之外的所有群（QUAL-016）。"""
    from app.models import BotDelivery

    first = BotTarget(name="一群", umo="qq:g1", enabled=True, config={})
    second = BotTarget(name="二群", umo="qq:g2", enabled=True, config={})
    disabled = BotTarget(name="停用群", umo="qq:g3", enabled=False, config={})
    session.add_all([first, second, disabled])
    await session.flush()
    brief, _ = await briefing.generate_brief(session, date(2026, 10, 17))

    result = await briefing.deliver_brief(session, brief)
    assert result["qq"] == 2  # 只数启用的，且每个目标各一条
    assert result["status"] == "sent"

    rows = list((await session.execute(select(BotDelivery))).scalars())
    assert sorted(row.target_id for row in rows) == sorted([first.id, second.id])
    assert {row.dedupe_key for row in rows} == {
        f"brief:{brief.id}:{first.id}",
        f"brief:{brief.id}:{second.id}",
    }
    for row in rows:
        assert "▎" in row.body  # 正文真的推进去了，不是空壳

    # 重推不重复建行，也不虚报计数
    again = await briefing.deliver_brief(session, brief)
    assert again["qq"] == 0
    assert len(list((await session.execute(select(BotDelivery))).scalars())) == 2


async def test_deliver_brief_marks_failed_without_channels(session):
    brief, _ = await briefing.generate_brief(session, date(2026, 10, 17))
    result = await briefing.deliver_brief(session, brief)
    assert result["status"] == "failed"
    assert "没有可用渠道" in (brief.error or "")


async def test_price_alert_webhook_only_reaches_the_owner(session, monkeypatch):
    """到价提醒是用户级事件：只投给设提醒者自己的 webhook，绝不广播全站（SEC-019）。"""
    from app.services import webhooks as webhooks_module

    class _Ok:
        status_code = 200
        text = "ok"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append(url)
            return _Ok()

    calls: list[str] = []
    monkeypatch.setattr(webhooks_module.httpx, "AsyncClient", _FakeClient)

    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.price_current = 188.0
    await session.commit()
    owner = await _user(session, email="owner@t.com")
    bystander = await _user(session, email="bystander@t.com")
    own_hook = WebhookConfig(user_id=owner.id, url="https://hooks.example.com/own", enabled=True)
    other_hook = WebhookConfig(
        user_id=bystander.id, url="https://hooks.example.com/other", enabled=True
    )
    session.add_all([own_hook, other_hook])
    await session.flush()
    await product_dossier.set_price_alert(session, owner.id, product.id, 200)

    result = await product_dossier.sweep_price_alerts(session)
    assert result["triggered"] == 1
    # 只有提醒所属用户的渠道收到 POST，另一个用户的 webhook 零请求
    assert calls == ["https://hooks.example.com/own"]
    from app.models import WebhookDelivery

    rows = list((await session.execute(select(WebhookDelivery))).scalars())
    assert [row.webhook_id for row in rows] == [own_hook.id]


async def test_run_daily_brief_end_to_end(session):
    now = utcnow()
    # 简报口径是「北京时间昨天」，跟着同一个函数算，避免被当前 UTC 小时带偏
    day = briefing._brief_day(now)
    midday = datetime(day.year, day.month, day.day, 12)
    await _content(session, external_id="e2e", title="鸣潮 手办", keywords=["鸣潮 手办"],
                   collected_at=midday, published_at=midday)
    result = await briefing.run_daily_brief(session, now)
    assert result["date"] == day.isoformat()
    assert result["created"] is True
    assert result["brief_id"]
    assert result["status"] == "failed"  # 没有收件人与机器人目标

    stored = await session.scalar(select(DailyBrief).where(DailyBrief.brief_date == day))
    assert stored is not None
    assert stored.stats["volume_total"] == 1
    assert stored.stats["events"] == 0


async def test_run_daily_brief_uses_beijing_yesterday(session):
    # 北京时间 08:30 之后跑，回顾的是「昨天」
    beijing_now = datetime(2026, 10, 18, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    result = await briefing.run_daily_brief(session, beijing_now)
    assert result["date"] == "2026-10-17"


async def test_list_and_get_briefs(session):
    await briefing.generate_brief(session, date(2026, 10, 16))
    await briefing.generate_brief(session, date(2026, 10, 17))
    body = await briefing.list_briefs(session)
    assert body["total"] == 2
    assert body["items"][0]["brief_date"].isoformat() == "2026-10-17"  # 最新在前

    filtered = await briefing.list_briefs(session, day=date(2026, 10, 16))
    assert filtered["total"] == 1

    detail = await briefing.get_brief(session, body["items"][0]["id"])
    assert detail is not None
    assert await briefing.get_brief(session, 9999) is None


async def test_preview_brief_does_not_persist(session):
    body = await briefing.preview_brief(session, date(2026, 10, 17))
    assert body["stored"] == 0
    assert body["sections"]["volume"]["total"] == 0
    assert await session.scalar(select(DailyBrief)) is None


# ---------- 管理端接口 ----------


async def test_admin_dossier_endpoints(client, session, admin_headers):
    product = await _product(session)

    listed = await client.get("/api/admin/product-dossiers", headers=admin_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["has_profile"] is False

    # 维护档案头
    updated = await client.put(
        f"/api/admin/product-dossiers/{product.id}",
        headers=admin_headers,
        json={
            "cover_url": "https://img/cover.jpg",
            "official_url": "https://official.example",
            "release_state": "presale",
            "release_at": "2026-12-01T10:00:00",
            "aliases": ["鸣潮手办", "鸣潮手办", ""],
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["cover_url"] == "https://img/cover.jpg"
    assert body["release_state"] == "presale"
    assert body["aliases"] == ["鸣潮手办"]  # 去重 + 去空
    assert body["release_at"].startswith("2026-12-01")

    # 空 payload 被拒
    empty = await client.put(
        f"/api/admin/product-dossiers/{product.id}", headers=admin_headers, json={"nope": 1}
    )
    assert empty.status_code == 400

    # 手工记价
    priced = await client.post(
        f"/api/admin/product-dossiers/{product.id}/prices",
        headers=admin_headers,
        json={"price": "268", "heat": 3, "hits": 2, "note": "外部渠道"},
    )
    assert priced.status_code == 201
    assert priced.json()["price"] == 268.0
    bad = await client.post(
        f"/api/admin/product-dossiers/{product.id}/prices",
        headers=admin_headers,
        json={"price": "abc"},
    )
    assert bad.status_code == 400

    # 详情
    detail = await client.get(
        f"/api/admin/product-dossiers/{product.id}", headers=admin_headers
    )
    assert detail.status_code == 200
    assert detail.json()["profile"]["price_current"] == 268.0
    assert len(detail.json()["series"]) == 1

    # 404
    missing = await client.get("/api/admin/product-dossiers/9999", headers=admin_headers)
    assert missing.status_code == 404


async def test_admin_dossier_aggregate_and_review(client, session, admin_headers):
    product = await _product(session)
    await _content(
        session,
        external_id="agg-1",
        title="鸣潮 手办掉漆了",
        text="这只鸣潮 手办涂装掉漆",
        keywords=["鸣潮 手办"],
    )
    result = await client.post(
        f"/api/admin/product-dossiers/{product.id}/feedback/aggregate", headers=admin_headers
    )
    assert result.status_code == 200
    assert result.json()["created"] == 1
    assert result.json()["ai"] is False

    reviewed = await client.patch(
        "/api/admin/product-dossiers/feedback/1",
        headers=admin_headers,
        json={"reviewed": True},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed"] is True
    missing = await client.patch(
        "/api/admin/product-dossiers/feedback/999", headers=admin_headers, json={"reviewed": True}
    )
    assert missing.status_code == 404


async def test_admin_dossier_sweeps(client, session, admin_headers):
    product = await _product(session)
    await _content(
        session,
        external_id="sweep-1",
        title="鸣潮 手办开订",
        text="鸣潮 手办定价 199",
        keywords=["鸣潮 手办"],
    )
    user = await _user(session)
    await product_dossier.set_price_alert(session, user.id, product.id, 250)

    prices = await client.post("/api/admin/product-dossiers/prices/sweep", headers=admin_headers)
    assert prices.status_code == 200
    assert prices.json()["points"] == 1

    alerts = await client.post("/api/admin/product-dossiers/alerts/sweep", headers=admin_headers)
    assert alerts.status_code == 200
    assert alerts.json()["triggered"] == 1

    ledger = await client.get("/api/admin/product-dossiers/alerts", headers=admin_headers)
    assert ledger.status_code == 200
    assert ledger.json()["total"] == 1
    assert ledger.json()["items"][0]["trigger_count"] == 1
    assert ledger.json()["items"][0]["product_name"] == "鸣潮 手办"

    heat = await client.post("/api/admin/product-dossiers/heat/refresh", headers=admin_headers)
    assert heat.status_code == 200
    assert heat.json()["updated"] == 1


async def test_admin_brief_endpoints(client, session, admin_headers):
    now = utcnow()
    yesterday = now - timedelta(days=1)
    await _content(session, external_id="brief-1", title="鸣潮 手办", keywords=["鸣潮 手办"],
                   intent_tier="important", collected_at=yesterday, published_at=yesterday)

    preview = await client.get(
        "/api/admin/briefs/preview", headers=admin_headers, params={"day": "2026-10-17"}
    )
    assert preview.status_code == 200
    assert preview.json()["stored"] == 0

    generated = await client.post(
        "/api/admin/briefs/generate", headers=admin_headers, json={"day": "2026-10-17"}
    )
    assert generated.status_code == 201
    assert generated.json()["created"] is True
    brief_id = generated.json()["id"]

    again = await client.post(
        "/api/admin/briefs/generate", headers=admin_headers, json={"day": "2026-10-17"}
    )
    assert again.json()["created"] is False  # 不重复生成

    listed = await client.get("/api/admin/briefs", headers=admin_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    detail = await client.get(f"/api/admin/briefs/{brief_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["email_subject"]
    assert "▎" in detail.json()["email_body"]

    sent = await client.post(f"/api/admin/briefs/{brief_id}/send", headers=admin_headers)
    assert sent.status_code == 200
    assert sent.json()["status"] == "failed"  # 无收件人与机器人目标

    missing = await client.get("/api/admin/briefs/9999", headers=admin_headers)
    assert missing.status_code == 404
    missing_send = await client.post("/api/admin/briefs/9999/send", headers=admin_headers)
    assert missing_send.status_code == 404


async def test_admin_brief_generate_validates_day(client, admin_headers):
    bad = await client.post(
        "/api/admin/briefs/generate", headers=admin_headers, json={"day": "not-a-date"}
    )
    assert bad.status_code == 422


async def test_admin_endpoints_require_token(client):
    for method, path in (
        ("get", "/api/admin/product-dossiers"),
        ("put", "/api/admin/product-dossiers/1"),
        ("post", "/api/admin/product-dossiers/1/prices"),
        ("post", "/api/admin/product-dossiers/alerts/sweep"),
        ("get", "/api/admin/product-dossiers/alerts"),
        ("get", "/api/admin/briefs"),
        ("get", "/api/admin/briefs/preview"),
        ("post", "/api/admin/briefs/generate"),
        ("get", "/api/admin/briefs/1"),
    ):
        response = await client.request(method, path)
        assert response.status_code in (401, 422), (method, path, response.status_code)


# ---------- 公开档案接口 ----------


async def test_public_dossier_endpoints(client, session):
    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.cover_url = "https://img/c.jpg"
    profile.official_url = "https://official.example"
    profile.release_at = utcnow() + timedelta(days=20)
    profile.release_state = "announced"
    await session.commit()
    await product_dossier.record_price_point(session, product.id, price=199.0, heat=4.0)
    session.add(
        ProductFeedback(product_id=product.id, summary="掉漆", sentiment="negative")
    )
    await session.commit()

    dossier = await client.get(f"/api/products/{product.slug}/dossier")
    assert dossier.status_code == 200
    body = dossier.json()
    assert body["profile"]["official_url"] == "https://official.example"
    assert len(body["series"]) == 1
    assert body["feedback"][0]["sentiment"] == "negative"

    series = await client.get(f"/api/products/{product.slug}/series", params={"days": 30})
    assert series.status_code == 200
    assert series.json()["items"][0]["price"] == 199.0

    feedback = await client.get(f"/api/products/{product.slug}/feedback")
    assert feedback.status_code == 200
    assert len(feedback.json()["items"]) == 1

    missing = await client.get("/api/products/nope/dossier")
    assert missing.status_code == 404
    missing_series = await client.get("/api/products/nope/series")
    assert missing_series.status_code == 404


async def test_public_release_calendar(client, session):
    product = await _product(session, slug="cal-1", name="日历商品")
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.release_at = utcnow() + timedelta(days=15)
    profile.release_state = "announced"
    await session.commit()

    body = await client.get("/api/release-calendar")
    assert body.status_code == 200
    assert [item["slug"] for item in body.json()["upcoming"]] == ["cal-1"]


# ---------- 账号中心接口 ----------


async def test_account_tracker_endpoints(client, session):
    await _register_and_login(client, session, email="track@t.com")
    user = await session.scalar(select(User).where(User.email == "track@t.com"))
    user.notification_enabled = True
    await session.commit()
    product = await _product(session)
    profile = await product_dossier.ensure_profile(session, product.id)
    profile.price_current = 199.0
    await session.commit()

    empty = await client.get("/api/account/product-tracker")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert [option["value"] for option in empty.json()["state_options"]] == [
        "watching", "ordered", "arrived", "dropped",
    ]

    # 未登录被拒
    from httpx import AsyncClient

    anon = AsyncClient(transport=client._transport, base_url="http://test")
    assert (await anon.get("/api/account/product-tracker")).status_code == 401
    await anon.aclose()

    # 标记状态
    marked = await client.put(
        f"/api/account/product-tracker/{product.id}/state",
        json={"state": "ordered", "note": "天猫预售"},
    )
    assert marked.status_code == 200
    assert marked.json()["state_label"] == "已预订"
    bad = await client.put(
        f"/api/account/product-tracker/{product.id}/state", json={"state": "不存在"}
    )
    assert bad.status_code == 400

    # 设心理价位
    alerted = await client.post(
        f"/api/account/product-tracker/{product.id}/price-alert", json={"target_price": 250}
    )
    assert alerted.status_code == 201
    assert alerted.json()["target_price"] == 250
    bad_price = await client.post(
        f"/api/account/product-tracker/{product.id}/price-alert", json={"target_price": -1}
    )
    assert bad_price.status_code == 400

    # 列表带上状态与提醒
    listed = await client.get("/api/account/product-tracker")
    item = listed.json()["items"][0]
    assert item["state"] == "ordered"
    assert item["price_current"] == 199.0
    assert item["alert"]["target_price"] == 250

    # 临时静音
    toggled = await client.patch(
        f"/api/account/product-tracker/{product.id}/price-alert", json={"enabled": False}
    )
    assert toggled.json()["enabled"] is False
    missing_toggle = await client.patch(
        f"/api/account/product-tracker/999/price-alert", json={"enabled": False}
    )
    assert missing_toggle.status_code == 404

    # 触发记录此时为空
    history = await client.get("/api/account/product-tracker/alerts/history")
    assert history.status_code == 200
    assert history.json()["items"] == []

    # 重新启用后扫描：触发记录出现，且 still_below 为真
    await client.patch(
        f"/api/account/product-tracker/{product.id}/price-alert", json={"enabled": True}
    )
    await product_dossier.sweep_price_alerts(session)
    history = await client.get("/api/account/product-tracker/alerts/history")
    assert history.json()["items"][0]["trigger_count"] == 1
    assert history.json()["items"][0]["still_below"] is True

    # 清理
    assert (
        await client.delete(f"/api/account/product-tracker/{product.id}/price-alert")
    ).status_code == 204
    assert (
        await client.delete(f"/api/account/product-tracker/{product.id}/state")
    ).status_code == 204
    assert (await client.get("/api/account/product-tracker")).json()["items"] == []


async def test_account_tracker_unknown_product(client, session):
    await _register_and_login(client, session, email="track2@t.com")
    resp = await client.put("/api/account/product-tracker/9999/state", json={"state": "ordered"})
    assert resp.status_code == 404
    resp = await client.post(
        "/api/account/product-tracker/9999/price-alert", json={"target_price": 100}
    )
    assert resp.status_code == 404
