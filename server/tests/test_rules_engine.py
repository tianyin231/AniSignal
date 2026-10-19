"""规则引擎（批次 3a）单测：布尔解析、规则动作执行、声量异常边界、信源分级、管理端 API。

覆盖用户要求的四类最低覆盖项，另加 fail-open 与管理端 CRUD 回归。
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import (
    BotDelivery,
    BotTarget,
    ContentVersion,
    NotificationDelivery,
    RawContent,
    Rule,
    RuleHit,
    Source,
    User,
    VolumeBaseline,
    WebhookConfig,
    WebhookDelivery,
)
from app.services import boolean_query
from app.services import rules as rules_module
from app.services import settings as settings_service
from app.services import source_tier
from app.services import volume as volume_module
from app.services import webhooks as webhooks_module

# ---------- 布尔关键词解析（Mention 风格 + 容错） ----------


@pytest.mark.parametrize(
    "expr,text,expected",
    [
        # 显式 AND / OR / NOT
        ("鸣潮 AND 联名", "鸣潮 × 某品牌联名公告", True),
        ("鸣潮 AND 联名", "鸣潮新版本", False),
        ("鸣潮 OR 绝区零", "绝区零新角色", True),
        ("鸣潮 OR 绝区零", "原神新角色", False),
        ("鸣潮 NOT 测试", "鸣潮正式上线", True),
        ("鸣潮 NOT 测试", "鸣潮测试服", False),
        # 相邻词默认 AND；逗号等同 OR
        ("鸣潮 联名", "鸣潮联名键盘", True),
        ("鸣潮 联名", "鸣潮新版本", False),
        ("鸣潮, 绝区零", "绝区零资讯", True),
        # Google 风格 +/- 前缀
        ("+鸣潮 -测试", "鸣潮正式服", True),
        ("+鸣潮 -测试", "鸣潮测试服", False),
        # 分组
        ("(鸣潮 OR 绝区零) AND 联名", "绝区零联名活动", True),
        ("(鸣潮 OR 绝区零) AND 联名", "绝区零新角色", False),
        # 引号短语按字面处理（其中的 AND 不是运算符）
        ('"鸣潮 AND 测试"', "鸣潮 AND 测试服开启", True),
        ('"鸣潮 AND 测试"', "鸣潮正式服", False),
        # 全角符号
        ("（鸣潮｜绝区零）＆联名", "鸣潮联名", True),
        ("鸣潮！测试", "鸣潮测试服", False),
    ],
)
def test_boolean_evaluation(expr, text, expected):
    assert boolean_query.evaluate(boolean_query.parse(expr), text) is expected


def test_empty_and_operator_only_expr_match_everything():
    """空表达式 / 只有运算符 → None（不限关键词），而不是报错。"""
    for expr in (None, "", "   ", "AND", "OR", "NOT", "&& ||", "()"):
        assert boolean_query.parse(expr) is None
        assert boolean_query.evaluate(boolean_query.parse(expr), "任何内容") is True


def test_malformed_expr_is_tolerated():
    """用户写错表达式不该让规则失效，只尽量还原意图。"""
    # 多余的右括号、未闭合的左括号
    assert boolean_query.evaluate(boolean_query.parse("鸣潮)) AND 联名"), "鸣潮联名") is True
    assert boolean_query.evaluate(boolean_query.parse("(鸣潮 AND 联名"), "鸣潮联名") is True
    # 悬空运算符被安全丢弃，剩下的部分照常生效
    assert boolean_query.evaluate(boolean_query.parse("鸣潮 AND AND 联名"), "鸣潮联名") is True
    assert boolean_query.evaluate(boolean_query.parse("鸣潮 OR"), "鸣潮资讯") is True
    assert boolean_query.evaluate(boolean_query.parse("鸣潮 NOT"), "鸣潮资讯") is True
    assert boolean_query.evaluate(boolean_query.parse("鸣潮 !"), "鸣潮资讯") is True
    # 未闭合引号：剩下全部当作短语
    assert boolean_query.evaluate(boolean_query.parse('"鸣潮联名'), "鸣潮联名公告") is True
    # 单个 | 与 ＆｜ 全角符号都按 OR/AND 处理
    assert boolean_query.evaluate(boolean_query.parse("鸣潮 | 绝区零"), "绝区零资讯") is True
    assert boolean_query.evaluate(boolean_query.parse("鸣潮 & 联名"), "鸣潮联名") is True


def test_describe_explains_parsed_expr():
    assert boolean_query.describe(None) == "不限关键词"
    assert boolean_query.describe(boolean_query.parse("鸣潮")) == "鸣潮"
    assert boolean_query.describe(boolean_query.parse("鸣潮 AND 联名")) == "鸣潮 AND 联名"
    assert boolean_query.describe(boolean_query.parse("(鸣潮 OR 绝区零) AND 联名")) == (
        "(鸣潮 OR 绝区零) AND 联名"
    )
    assert boolean_query.describe(boolean_query.parse("鸣潮 -测试")) == "鸣潮 AND NOT 测试"


# ---------- 测试基座 ----------


async def _source(
    session,
    *,
    external_id: str = "src-1",
    name: str = "鸣潮官方",
    follower_count: int | None = None,
    identity: str = "official",
    platform: str = "bilibili",
) -> Source:
    source = Source(
        platform=platform,
        external_id=external_id,
        name=name,
        identity=identity,
        follower_count=follower_count,
    )
    session.add(source)
    await session.flush()
    return source


async def _content(
    session,
    *,
    external_id: str = "BV1",
    title: str = "鸣潮新版本官宣",
    text: str = "正文内容",
    keywords: list[str] | None = None,
    platform: str = "bilibili",
    community: str = "wuwa",
    intent_tier: str | None = None,
    source: Source | None = None,
    category: str = "official_announce",
    collected_at: datetime | None = None,
) -> RawContent:
    raw = RawContent(
        platform=platform,
        content_type="video",
        external_id=external_id,
        source_id=source.id if source else None,
        url=f"https://example.com/{external_id}",
        published_at=utcnow() - timedelta(hours=2),
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
            normalized_json={},
            category=category,
        )
    )
    await session.commit()
    return raw


async def _rule(session, **kwargs) -> Rule:
    payload = {
        "name": "联名预警",
        "enabled": True,
        "priority": 100,
        "keyword_expr": "鸣潮 AND 联名",
        "actions": [{"type": "tag", "value": "联名"}],
    }
    payload.update(kwargs)
    rule = Rule(**payload)
    session.add(rule)
    await session.commit()
    return rule


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClients:
    """桩 httpx.AsyncClient：按 URL 排队返回响应，并记录每次 POST。"""

    def __init__(self, outcomes: dict[str, list]):
        self.outcomes = outcomes
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, json: dict = None, headers: dict = None):
        queue = self.outcomes.get(url) or [_FakeResponse(500)]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        self.calls.append({"url": url, "json": json, "headers": headers})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _stub_post(monkeypatch, outcomes) -> _FakeClients:
    fake = _FakeClients(outcomes)
    monkeypatch.setattr(webhooks_module.httpx, "AsyncClient", fake)
    return fake


# ---------- 规则动作执行 ----------


async def test_tag_and_mark_read_actions(session):
    content = await _content(session, title="鸣潮联名键盘开售")
    rule = await _rule(
        session,
        actions=[
            {"type": "tag", "value": "联名"},
            {"type": "tag", "value": "联名"},  # 重复标签不叠加
            {"type": "mark_read"},
        ],
    )

    stats = await rules_module.apply_rules(session, [content.id])

    assert stats.matched == 1
    assert stats.actions == 3  # 两个 tag 动作 + mark_read
    assert stats.errors == 0
    await session.refresh(content)
    assert content.rule_tags == ["联名"]  # 重复标签不叠加
    assert content.rule_acked is True
    assert content.rule_acked_at is not None
    hit = (await session.execute(select(RuleHit))).scalar_one()
    assert hit.rule_id == rule.id
    assert hit.error is None
    assert "打标签 联名" in hit.detail and "标记已读" in hit.detail
    await session.refresh(rule)
    assert rule.hit_count == 1
    assert rule.last_hit_at is not None
    assert rule.last_error is None


async def test_rule_not_matching_condition_does_nothing(session):
    content = await _content(session, title="鸣潮新版本前瞻")
    await _rule(session, keyword_expr="鸣潮 AND 联名")

    stats = await rules_module.apply_rules(session, [content.id])

    assert stats.contents == 1
    assert stats.matched == 0
    assert stats.actions == 0
    assert (await session.execute(select(func.count()).select_from(RuleHit))).scalar() == 0


async def test_condition_scopes_platform_community_and_intent_tier(session):
    content = await _content(
        session, title="鸣潮联名", platform="miyoushe", community="wuwa", intent_tier="normal"
    )
    # 平台 / 分区 / 重要性档全部不符
    await _rule(session, keyword_expr="联名", platforms=["bilibili"])
    await _rule(session, keyword_expr="联名", communities=["blhx"])
    await _rule(session, keyword_expr="联名", intent_tiers=["important"])

    stats = await rules_module.apply_rules(session, [content.id])
    assert stats.matched == 0

    # 三项都放开后命中
    await _rule(session, keyword_expr="联名", platforms=["miyoushe"],
                communities=["wuwa"], intent_tiers=["normal"])
    stats = await rules_module.apply_rules(session, [content.id])
    assert stats.matched == 1


async def test_keyword_expr_is_parsed_once_per_rule(session, monkeypatch):
    """keyword_expr 每条规则只 parse 一次，不是 contents × rules 次（SEC-022 建议 4）。

    表达式最长 1000 字符、规则数不可控，批量回填时反复 parse 是纯浪费。
    """
    for index in range(3):
        await _content(session, external_id=f"BVparse{index}", title="鸣潮联名官宣")
    contents = list((await session.execute(select(RawContent))).scalars())
    rules = [await _rule(session, name=f"规则{index}", priority=index) for index in range(4)]

    calls: list[str | None] = []
    original = rules_module.boolean_query.parse

    def counting_parse(expr):
        calls.append(expr)
        return original(expr)

    monkeypatch.setattr(rules_module.boolean_query, "parse", counting_parse)
    stats = await rules_module.apply_rules(session, [content.id for content in contents])

    # 容错语义没变：4 条规则 × 3 条内容全命中
    assert stats.matched == len(contents) * len(rules)
    assert calls == [rule.keyword_expr for rule in rules]


async def test_promote_event_action_creates_event(session):
    source = await _source(session)
    content = await _content(session, external_id="BVP1", title="鸣潮联名官宣", source=source)
    await _rule(session, actions=[{"type": "promote_event"}])

    stats = await rules_module.apply_rules(session, [content.id])

    assert stats.events == 1
    hit = (await session.execute(select(RuleHit))).scalar_one()
    assert "转为事件 #" in hit.detail
    from app.models import Evidence

    evidence = (await session.execute(select(Evidence))).scalar_one()
    assert evidence.raw_content_id == content.id


async def _bot_target(session, *, name: str = "QQ 群") -> BotTarget:
    target = BotTarget(name=name, umo=f"qq:group:{name}", config={}, enabled=True)
    session.add(target)
    await session.commit()
    return target


async def test_notify_action_pushes_webhook_and_qq(session, monkeypatch):
    source = await _source(session, follower_count=1_500_000)
    content = await _content(
        session, external_id="BVN1", title="鸣潮联名官宣", keywords=["鸣潮"], source=source
    )
    user = User(
        email="hook@t.com",
        password_hash="x",
        is_verified=True,
        notification_enabled=True,
        status="active",
    )
    session.add(user)
    await session.flush()
    hook = WebhookConfig(user_id=user.id, url="https://hooks.example.com/n8n", tier_filter="all")
    session.add(hook)
    await _bot_target(session)
    fake = _stub_post(monkeypatch, {"https://hooks.example.com/n8n": [_FakeResponse(200)]})
    await _rule(
        session,
        actions=[{"type": "notify", "channels": ["webhook", "qq"]}],
    )

    stats = await rules_module.apply_rules(session, [content.id])

    assert stats.notified == 2  # webhook 一封 + QQ 一个目标
    assert stats.errors == 0
    assert len(fake.calls) == 1
    payload = fake.calls[0]["json"]
    assert payload["title"] == "[高权重] 鸣潮联名官宣"  # 信源分级标注进通知
    assert payload["tier"] == "normal"
    deliveries = list((await session.execute(select(WebhookDelivery))).scalars())
    assert [row.status for row in deliveries] == ["sent"]
    assert deliveries[0].dedupe_key == f"rule:1:content:{content.id}:webhook:{hook.id}"
    assert deliveries[0].kind == "rule"
    bot_rows = list((await session.execute(select(BotDelivery))).scalars())
    assert len(bot_rows) == 1
    assert bot_rows[0].kind == "critical"


async def test_notify_defaults_to_qq_when_channels_missing(session):
    """没选渠道时退回机器人直推，规则不至于静默失效。"""
    content = await _content(session, external_id="BVN2", title="鸣潮联名官宣")
    await _rule(session, actions=[{"type": "notify"}])

    stats = await rules_module.apply_rules(session, [content.id])

    assert stats.notified == 0  # 没有启用的机器人目标，推送数 0
    hit = (await session.execute(select(RuleHit))).scalar_one()
    assert hit.actions[0] == {"type": "notify", "channels": ["qq"]}


async def test_email_channel_creates_delivery_with_rule_notice(session):
    content = await _content(session, external_id="BVE1", title="鸣潮联名官宣")
    user = User(
        email="mail@t.com",
        password_hash="x",
        is_verified=True,
        notification_enabled=True,
        status="active",
    )
    session.add(user)
    await session.commit()
    await _rule(session, name="联名播报", actions=[{"type": "notify", "channels": ["email"]}])

    stats = await rules_module.apply_rules(session, [content.id])

    rows = list((await session.execute(select(NotificationDelivery))).scalars())
    assert len(rows) == 1
    assert rows[0].user_id == user.id
    assert rows[0].source == "rules"
    assert rows[0].subject == "[AniSignal 规则] 联名播报"
    assert "规则「联名播报」命中一条内容" in rows[0].body
    assert "鸣潮联名官宣" in rows[0].body
    # 测试环境没有 SMTP，发送失败但投递行可见（管理端可重发）
    assert rows[0].status == "failed"
    assert stats.notified == 0


def _mail_user(session, index: int) -> User:
    user = User(
        email=f"mail{index}@t.com",
        password_hash="x",
        is_verified=True,
        notification_enabled=True,
        status="active",
    )
    session.add(user)
    return user


async def test_email_channel_reaches_every_recipient_once(session):
    """每个收件人一行投递、key 各带 user_id；重跑不重复建行（PERF-012 重构后的口径）。"""
    content = await _content(session, external_id="BVEmulti", title="鸣潮联名官宣")
    users = [_mail_user(session, i) for i in range(3)]
    session.add_all(users)
    await session.flush()
    await _rule(session, name="联名播报", actions=[{"type": "notify", "channels": ["email"]}])

    stats = await rules_module.apply_rules(session, [content.id])

    rows = list((await session.execute(select(NotificationDelivery))).scalars())
    assert sorted(row.user_id for row in rows) == sorted(u.id for u in users)
    assert {row.dedupe_key for row in rows} == {
        f"rule:1:content:{content.id}:email:{u.id}" for u in users
    }
    assert all(row.source == "rules" and row.subject == "[AniSignal 规则] 联名播报" for row in rows)

    # 重跑：去重键已在，不新增行也不虚报
    stats = await rules_module.apply_rules(session, [content.id])
    assert stats.notified == 0
    assert len(list((await session.execute(select(NotificationDelivery))).scalars())) == 3


async def test_email_channel_truncates_recipients(session):
    """收件人截断到 MAX_EMAIL_RECIPIENTS：同步 SMTP 不能把采集进程堵死（PERF-012）。

    注意截断是「本轮」语义：(规则, 内容) 记账成功后不会重跑，所以排在截断线之外的人
    这条内容收不到——这是有意的取舍，彻底解法是把投递挪进异步队列（见报告 PERF-012）。
    """
    content = await _content(session, external_id="BVEcap", title="鸣潮联名官宣")
    session.add_all([_mail_user(session, i) for i in range(rules_module.MAX_EMAIL_RECIPIENTS + 5)])
    await session.flush()
    await _rule(session, name="联名播报", actions=[{"type": "notify", "channels": ["email"]}])

    await rules_module.apply_rules(session, [content.id])

    rows = list((await session.execute(select(NotificationDelivery))).scalars())
    assert len(rows) == rules_module.MAX_EMAIL_RECIPIENTS
    assert len({row.user_id for row in rows}) == rules_module.MAX_EMAIL_RECIPIENTS

    # 记账成功后不重跑，也不会重复发信
    await rules_module.apply_rules(session, [content.id])
    rows = list((await session.execute(select(NotificationDelivery))).scalars())
    assert len(rows) == rules_module.MAX_EMAIL_RECIPIENTS


async def test_rule_hit_is_idempotent_and_failed_rule_retries(session):
    """已成功记账的组合不重复执行；失败的组合下轮重试（fail-open 但可恢复）。"""
    content = await _content(session, external_id="BVD1", title="鸣潮联名官宣")
    await _rule(session, keyword_expr="鸣潮 AND 官宣")

    first = await rules_module.apply_rules(session, [content.id])
    second = await rules_module.apply_rules(session, [content.id])
    assert first.matched == 1
    assert second.matched == 0
    assert (await session.execute(select(func.count()).select_from(RuleHit))).scalar() == 1

    # 动作执行失败：记账带 error，下一轮会重试
    content2 = await _content(session, external_id="BVD2", title="鸣潮联名公告")
    broken = await _rule(
        session, name="坏规则", actions=[{"type": "tag", "value": "x"}], priority=1
    )
    good = await _rule(session, name="好规则", actions=[{"type": "tag", "value": "y"}], priority=2)
    original = rules_module._execute_actions

    async def _boom(session_, rule, ctx, actions, cfg, event_map, stats):
        if rule.id == broken.id:
            raise RuntimeError("模拟动作失败")
        return await original(session_, rule, ctx, actions, cfg, event_map, stats)

    rules_module._execute_actions = _boom
    try:
        stats = await rules_module.apply_rules(session, [content2.id])
    finally:
        rules_module._execute_actions = original

    assert stats.matched == 1  # 好规则照常执行（坏规则只跳过自己）
    assert stats.errors == 1
    await session.refresh(broken)
    assert broken.last_error and "动作执行失败" in broken.last_error
    failed = (
        await session.execute(select(RuleHit).where(RuleHit.rule_id == broken.id))
    ).scalar_one()
    assert failed.error and "模拟动作失败" in failed.error
    assert failed.actions == []
    assert (await session.execute(select(func.count()).select_from(RuleHit))).scalar() == 3

    # 恢复后同一组合重试成功
    stats = await rules_module.apply_rules(session, [content2.id])
    assert stats.matched == 1
    assert stats.errors == 0
    assert (await session.execute(select(func.count()).select_from(RuleHit))).scalar() == 3
    assert (await session.execute(select(func.count()).select_from(RuleHit).where(RuleHit.error.is_not(None)))).scalar() == 0


async def test_rules_disabled_by_config_skips_everything(session):
    content = await _content(session, title="鸣潮联名官宣")
    await _rule(session)

    stats = await rules_module.apply_rules(
        session, [content.id], config={"enabled": False}
    )

    assert stats.matched == 0
    assert stats.contents == 0
    assert (await session.execute(select(func.count()).select_from(RuleHit))).scalar() == 0


# ---------- 声量异常告警 ----------


async def _volume_rows(
    session, *, platform: str = "bilibili", keyword: str = "鸣潮", start: datetime, minutes: int, counts: list[int]
) -> list[VolumeBaseline]:
    rows = []
    for index, count in enumerate(counts):
        row = VolumeBaseline(
            group_key=volume_module.group_key(platform, keyword),
            platform=platform,
            keyword=keyword,
            window_started_at=start + timedelta(minutes=minutes * index),
            window_minutes=minutes,
            hit_count=count,
            mean=0.0,
            stddev=0.0,
            samples=0,
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    return rows


async def _contents_at(
    session, *, count: int, at: datetime, platform: str = "bilibili", keyword: str = "鸣潮"
) -> None:
    for index in range(count):
        await _content(
            session,
            external_id=f"V{keyword}{int(at.timestamp())}-{index}",
            title=f"{keyword}动态 {index}",
            keywords=[keyword],
            platform=platform,
            collected_at=at,
        )


async def test_is_anomaly_boundaries():
    """正好 N 倍不算；样本/命中数不足一律不告警。"""
    # mean=2, multiplier=3 → 阈值 6；6 不算，7 才算
    assert volume_module.is_anomaly(7, 2.0, multiplier=3.0, min_samples=0, min_hits=0) is True
    assert volume_module.is_anomaly(6, 2.0, multiplier=3.0, min_samples=0, min_hits=0) is False
    assert volume_module.is_anomaly(5, 2.0, multiplier=3.0, min_samples=0, min_hits=0) is False
    # 命中数不足（4 条已超过均值 3 倍，但低于 min_hits）
    assert volume_module.is_anomaly(4, 1.0, multiplier=3.0, min_samples=0, min_hits=5) is False
    # 基线样本不足（刚上线）：samples 必须显式参与判定
    assert (
        volume_module.is_anomaly(
            99, 2.0, multiplier=3.0, min_samples=5, min_hits=0, samples=4
        )
        is False
    )
    assert (
        volume_module.is_anomaly(
            99, 2.0, multiplier=3.0, min_samples=5, min_hits=0, samples=5
        )
        is True
    )
    # 没有历史基线（均值为 0）
    assert volume_module.is_anomaly(99, 0.0, multiplier=3.0, min_samples=1, min_hits=0) is False


async def test_sweep_marks_anomaly_and_notifies_critical(session, monkeypatch):
    """超过基线 N 倍 → critical 热度预警（QQ + OnePush + webhook）。"""
    config = {
        "window_minutes": 60,
        "history_windows": 24,
        "min_samples": 3,
        "min_hits": 5,
        "anomaly_multiplier": 3.0,
        "cooldown_minutes": 0,
    }
    now = datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)
    window_start = volume_module.align_window(now, 60)
    await _volume_rows(
        session, start=window_start - timedelta(minutes=60 * 4), minutes=60, counts=[2, 3, 2, 3]
    )
    await _contents_at(session, count=20, at=now)
    await _bot_target(session)
    user = User(
        email="hook@t.com",
        password_hash="x",
        is_verified=True,
        notification_enabled=True,
        status="active",
    )
    session.add(user)
    await session.flush()
    hook = WebhookConfig(user_id=user.id, url="https://hooks.example.com/n8n", tier_filter="all")
    session.add(hook)
    await session.commit()
    fake = _stub_post(monkeypatch, {"https://hooks.example.com/n8n": [_FakeResponse(200)]})

    result = await volume_module.sweep_volume(session, now, config=config)

    assert result["anomalies"] == 1
    assert result["notified"] == 2  # QQ 1 + webhook 1（OnePush 未配置不计）
    assert len(fake.calls) == 1
    assert fake.calls[0]["json"]["tier"] == "critical"
    assert "热度预警" in fake.calls[0]["json"]["title"]
    row = (
        await session.execute(
            select(VolumeBaseline).where(
                VolumeBaseline.window_started_at == window_start
            )
        )
    ).scalar_one()
    assert row.hit_count == 20
    assert row.mean == 2.5
    assert row.status == "anomaly"
    assert row.notified_at is not None
    deliveries = list((await session.execute(select(BotDelivery))).scalars())
    assert [row_.kind for row_ in deliveries] == ["critical"]
    assert "热度预警" in deliveries[0].body


async def test_sweep_does_not_alert_at_or_below_threshold(session):
    """命中数正好是基线 3 倍（甚至更低）时不告警。"""
    config = {
        "window_minutes": 60,
        "history_windows": 24,
        "min_samples": 3,
        "min_hits": 5,
        "anomaly_multiplier": 3.0,
        "cooldown_minutes": 0,
    }
    now = datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)
    window_start = volume_module.align_window(now, 60)
    # 历史均值 2.5 → 阈值 7.5；本窗 7 条不算异常
    await _volume_rows(
        session, start=window_start - timedelta(minutes=60 * 4), minutes=60, counts=[2, 3, 2, 3]
    )
    await _contents_at(session, count=7, at=now)

    result = await volume_module.sweep_volume(session, now, config=config)

    assert result["anomalies"] == 0
    assert result["notified"] == 0
    row = (
        await session.execute(
            select(VolumeBaseline).where(VolumeBaseline.window_started_at == window_start)
        )
    ).scalar_one()
    assert row.status == "normal"
    assert row.notified_at is None
    assert (await session.execute(select(func.count()).select_from(BotDelivery))).scalar() == 0


async def test_sweep_skips_when_history_samples_insufficient(session):
    """样本不足（刚上线）不告警，但基线照常记录。"""
    config = {
        "window_minutes": 60,
        "history_windows": 24,
        "min_samples": 5,
        "min_hits": 5,
        "anomaly_multiplier": 3.0,
        "cooldown_minutes": 0,
    }
    now = datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)
    window_start = volume_module.align_window(now, 60)
    await _volume_rows(
        session, start=window_start - timedelta(minutes=60 * 2), minutes=60, counts=[1, 1]
    )
    await _contents_at(session, count=50, at=now)

    result = await volume_module.sweep_volume(session, now, config=config)

    assert result["windows"] == 1
    assert result["anomalies"] == 0
    row = (
        await session.execute(
            select(VolumeBaseline).where(VolumeBaseline.window_started_at == window_start)
        )
    ).scalar_one()
    assert row.samples == 2
    assert row.status == "normal"


async def test_sweep_honours_cooldown(session):
    """同一分组在冷却期内不重复预警。"""
    config = {
        "window_minutes": 60,
        "history_windows": 24,
        "min_samples": 3,
        "min_hits": 5,
        "anomaly_multiplier": 3.0,
        "cooldown_minutes": 120,
    }
    now = datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)
    window_start = volume_module.align_window(now, 60)
    await _volume_rows(
        session, start=window_start - timedelta(minutes=60 * 4), minutes=60, counts=[2, 3, 2, 3]
    )
    await _contents_at(session, count=30, at=now)
    await _bot_target(session)
    await session.commit()

    first = await volume_module.sweep_volume(session, now, config=config)
    # 同一窗口再来一次（ notified_at 刚写入，仍在冷却期）
    second = await volume_module.sweep_volume(session, now, config=config)

    assert first["anomalies"] == 1
    assert first["notified"] == 1
    assert second["anomalies"] == 1  # 仍判定为异常
    assert second["notified"] == 0  # 但不再打扰


async def test_sweep_without_hits_is_a_noop(session):
    result = await volume_module.sweep_volume(
        session, datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)
    )
    assert result == {"groups": 0, "windows": 0, "anomalies": 0, "notified": 0}


async def test_align_window_buckets_to_window_boundary():
    base = datetime(2026, 10, 18, 12, 47, tzinfo=timezone.utc)
    assert volume_module.align_window(base, 60) == datetime(2026, 10, 18, 12, 0)
    assert volume_module.align_window(base, 30) == datetime(2026, 10, 18, 12, 30)


# ---------- 信源分级 ----------


def test_classify_by_follower_thresholds():
    thresholds = {"mid": 100_000, "high": 1_000_000}
    assert source_tier.classify(None, thresholds) == "unknown"
    assert source_tier.classify(0, thresholds) == "unknown"
    assert source_tier.classify(99_999, thresholds) == "low"
    assert source_tier.classify(100_000, thresholds) == "mid"
    assert source_tier.classify(999_999, thresholds) == "mid"
    assert source_tier.classify(1_000_000, thresholds) == "high"


def test_thresholds_are_normalized_and_ordered():
    assert source_tier.normalize_thresholds(None) == {"mid": 100_000, "high": 1_000_000}
    assert source_tier.normalize_thresholds({"mid": "50", "high": "abc"}) == {
        "mid": 50,
        "high": 1_000_000,
    }
    # mid > high 时夹紧，避免阈值写反导致没有高权重源
    assert source_tier.normalize_thresholds({"mid": 500_000, "high": 100_000}) == {
        "mid": 100_000,
        "high": 100_000,
    }


def test_tier_weight_and_label():
    assert source_tier.weight("high") > source_tier.weight("mid")
    assert source_tier.weight("mid") > source_tier.weight("low")
    assert source_tier.weight("low") > source_tier.weight("unknown")
    assert source_tier.weight(None) == 0
    assert source_tier.label("high") == "高权重"
    assert source_tier.label("bogus") == "未分级"


async def test_refresh_source_tiers_updates_changed_rows(session):
    low = await _source(session, external_id="low", follower_count=1_000)
    mid = await _source(session, external_id="mid", follower_count=200_000)
    high = await _source(session, external_id="high", follower_count=3_000_000)
    none_ = await _source(session, external_id="none")

    changed = await source_tier.refresh_source_tiers(
        session, [low.id, mid.id, high.id, none_.id]
    )

    assert changed == {low.id: "low", mid.id: "mid", high.id: "high"}
    assert none_.tier == "unknown"
    # 幂等：第二次没有变化
    assert await source_tier.refresh_source_tiers(session, [low.id, mid.id]) == {}


async def test_tiers_for_contents(session):
    source = await _source(session, follower_count=1_500_000)
    with_source = await _content(session, external_id="T1", source=source)
    without = await _content(session, external_id="T2")
    await source_tier.refresh_source_tiers(session, [source.id])

    mapping = await source_tier.tiers_for_contents(session, [with_source.id, without.id])

    assert mapping[with_source.id] == ("high", "鸣潮官方")
    assert mapping[without.id] == ("unknown", None)


# ---------- 配置 ----------


async def test_rules_config_round_trip_and_clamping(session):
    await settings_service.set_setting(session, "rules", {"anomaly_multiplier": "abc"})
    config = await settings_service.get_rules_config(session)
    assert config["anomaly_multiplier"] == 3.0  # 脏值回落默认
    assert config["window_minutes"] == 60
    assert config["source_tiers"] == {"mid": 100_000, "high": 1_000_000}

    await settings_service.set_setting(
        session,
        "rules",
        {"anomaly_multiplier": 999, "window_minutes": 15, "source_tiers": {"mid": 5, "high": 9}},
    )
    config = await settings_service.get_rules_config(session)
    assert config["anomaly_multiplier"] == 20.0  # 上限夹紧
    assert config["window_minutes"] == 15
    assert config["source_tiers"] == {"mid": 5, "high": 9}


# ---------- 管理端 API ----------


def assert_admin_token_required(response, method: str, path: str) -> None:
    """SEC-039：精确断言"缺管理员令牌被挡下"，不用 `in (401, 422)` 区间。

    `require_admin` 是 `Header(...)` 形式的必需 header，缺令牌时 FastAPI 走校验层返回
    422——这与"body 缺字段"的 422 状态码相同，只有 `loc` 不同。区间断言会把两种都算过：
    把 `Depends(require_admin)` 从路由上摘掉时，GET 会变 200、POST 仍会因缺 body 返回
    422，`in (401, 422)` 依然全绿，鉴权回归被完全掩护。所以必须核对 loc 里真的出现了
    header/x-admin-token。
    """
    assert response.status_code == 422, (method, path, response.status_code, response.text)
    detail = response.json()["detail"]
    assert any(err.get("loc") == ["header", "x-admin-token"] for err in detail), (
        f"{method} {path} 的响应里没有 x-admin-token 缺失错误，"
        f"鉴权依赖可能已被摘掉或写反：{detail}"
    )


async def test_admin_rule_crud_and_hit_ledger(client, session, admin_headers):
    created = await client.post(
        "/api/admin/rules-engine",
        headers=admin_headers,
        json={
            "name": "联名预警",
            "keyword_expr": "鸣潮 AND 联名",
            "actions": [{"type": "tag", "value": "联名"}, {"type": "notify", "channels": ["webhook"]}],
            "platforms": ["bilibili", "bilibili"],  # 去重
        },
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    assert created.json()["parsed_expr"] == "鸣潮 AND 联名"
    assert created.json()["platforms"] == ["bilibili"]

    listed = await client.get("/api/admin/rules-engine", headers=admin_headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "联名预警"

    # 局部更新：未传的字段保持原样
    updated = await client.put(
        f"/api/admin/rules-engine/{rule_id}",
        headers=admin_headers,
        json={"priority": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["priority"] == 1
    assert updated.json()["enabled"] is True  # 未传字段不动
    assert updated.json()["keyword_expr"] == "鸣潮 AND 联名"

    # 校验：未知渠道 / 缺 value 的打标签动作 / 未知重要性档
    bad_channel = await client.post(
        "/api/admin/rules-engine",
        headers=admin_headers,
        json={"name": "坏渠道", "actions": [{"type": "notify", "channels": ["sms"]}]},
    )
    assert bad_channel.status_code == 422
    bad_tag = await client.post(
        "/api/admin/rules-engine",
        headers=admin_headers,
        json={"name": "坏标签", "actions": [{"type": "tag"}]},
    )
    assert bad_tag.status_code == 422
    bad_tier = await client.post(
        "/api/admin/rules-engine",
        headers=admin_headers,
        json={"name": "坏档位", "intent_tiers": ["urgent"]},
    )
    assert bad_tier.status_code == 422

    # 命中台账
    content = await _content(session, external_id="BVAPI", title="鸣潮联名官宣")
    stats = await rules_module.apply_rules(session, [content.id])
    assert stats.matched == 1
    # 停用后同一条内容再跑一遍：规则不再执行，但历史命中记录仍在
    await client.put(
        f"/api/admin/rules-engine/{rule_id}",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert (await rules_module.apply_rules(session, [content.id])).matched == 0
    hits = await client.get(
        "/api/admin/rules-engine/hits",
        headers=admin_headers,
        params={"rule_id": rule_id},
    )
    assert hits.status_code == 200
    items = hits.json()["items"]
    assert len(items) == 1
    assert items[0]["content_id"] == content.id
    assert items[0]["rule_name"] == "联名预警"
    assert items[0]["content_title"] == "鸣潮联名官宣"

    deleted = await client.delete(f"/api/admin/rules-engine/{rule_id}", headers=admin_headers)
    assert deleted.status_code == 204
    missing = await client.put(
        f"/api/admin/rules-engine/{rule_id}", headers=admin_headers, json={"enabled": True}
    )
    assert missing.status_code == 404


async def test_admin_endpoints_require_token(client):
    for method, path in (
        ("get", "/api/admin/rules-engine"),
        ("post", "/api/admin/rules-engine"),
        ("get", "/api/admin/rules-engine/hits"),
        ("get", "/api/admin/rules-engine/volume"),
        ("get", "/api/admin/rules-engine/config"),
    ):
        response = await client.request(method, path)
        assert_admin_token_required(response, method, path)


async def test_admin_volume_and_config_endpoints(client, session, admin_headers):
    # 用真实时钟对齐当前窗口（sweep 内部用的是 utcnow）
    now = volume_module.align_window(utcnow(), 60) + timedelta(minutes=30)
    window_start = volume_module.align_window(now, 60)
    # 历史 4 个窗口均值 2（作为基线样本）
    await _volume_rows(
        session, start=window_start - timedelta(minutes=60 * 4), minutes=60, counts=[2, 2, 2, 2]
    )
    await _contents_at(session, count=3, at=now)

    listed = await client.get("/api/admin/rules-engine/volume", headers=admin_headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 4  # 只有历史基线，当前窗口还没扫
    assert all(item["status"] == "normal" for item in body["items"])

    filtered = await client.get(
        "/api/admin/rules-engine/volume",
        headers=admin_headers,
        params={"status": "anomaly"},
    )
    assert filtered.json()["total"] == 0

    config = await client.put(
        "/api/admin/rules-engine/config",
        headers=admin_headers,
        json={"anomaly_multiplier": 5},
    )
    assert config.status_code == 200
    assert config.json()["anomaly_multiplier"] == 5
    assert config.json()["window_minutes"] == 60  # 未传字段保持默认
    assert config.json()["min_hits"] == 5

    fetched = await client.get("/api/admin/rules-engine/config", headers=admin_headers)
    assert fetched.json() == config.json()

    # 手动扫描：当前窗口 3 条，低于 min_hits=5，不告警
    swept = await client.post("/api/admin/rules-engine/volume/sweep", headers=admin_headers)
    assert swept.status_code == 200
    assert swept.json()["windows"] == 1
    assert swept.json()["anomalies"] == 0

    after = await client.get(
        "/api/admin/rules-engine/volume", headers=admin_headers, params={"page_size": 1}
    )
    item = after.json()["items"][0]
    assert item["window_started_at"].startswith(window_start.strftime("%Y-%m-%dT%H:%M"))
    assert item["hit_count"] == 3
    assert item["mean"] == 2.0
    assert item["samples"] == 4
    assert item["ratio"] == 1.5


async def test_admin_source_tier_refresh_endpoint(client, session, admin_headers):
    await _source(session, external_id="s-low", follower_count=1_000)
    await _source(session, external_id="s-mid", follower_count=300_000)
    await _source(session, external_id="s-high", follower_count=2_000_000)

    result = await client.post(
        "/api/admin/rules-engine/source-tiers/refresh", headers=admin_headers
    )

    assert result.status_code == 200
    body = result.json()
    assert body["scanned"] == 3
    assert body["changed"] == 3
    assert body["by_tier"] == {"low": 1, "mid": 1, "high": 1}
    assert body["distribution"] == {"low": 1, "mid": 1, "high": 1}

    # 阈值改小后重跑：档位随之变化（1000 → mid，30w → high）
    await client.put(
        "/api/admin/rules-engine/config",
        headers=admin_headers,
        json={"source_tiers": {"mid": 500, "high": 5_000}},
    )
    again = await client.post(
        "/api/admin/rules-engine/source-tiers/refresh", headers=admin_headers
    )
    body = again.json()
    assert body["changed"] == 2
    assert body["by_tier"] == {"mid": 1, "high": 1}
    assert body["distribution"] == {"mid": 1, "high": 2}


async def test_admin_rule_preview_reports_matches_without_acting(client, session, admin_headers):
    await _content(session, external_id="BVP1", title="鸣潮联名键盘")
    await _content(session, external_id="BVP2", title="鸣潮新版本前瞻")

    preview = await client.post(
        "/api/admin/rules-engine/preview",
        headers=admin_headers,
        json={"name": "联名", "keyword_expr": "鸣潮 AND 联名"},
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["scanned"] == 2
    assert body["matched"] == 1
    assert body["parsed_expr"] == "鸣潮 AND 联名"
    assert body["items"][0]["title"] == "鸣潮联名键盘"
    assert "联名" in body["items"][0]["reason"]
    # 试跑不执行任何动作
    assert (await session.execute(select(func.count()).select_from(RuleHit))).scalar() == 0
