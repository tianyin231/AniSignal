"""意图打标单测：解析容错、fail-open、用量记账、采集三档链路、机器人守卫与管理端 API。"""

import json
from datetime import timedelta

from sqlalchemy import func, select

from app.clock import utcnow
from app.models import (
    AiUsage,
    BotDelivery,
    BotTarget,
    CollectRun,
    CollectTarget,
    ContentVersion,
    Event,
    Evidence,
    RawContent,
)
from app.services import bot as bot_module
from app.services import intent as intent_module
from app.schemas.content import FetchResult

import app.services.collect as collect_module

from tests.test_collect_service import _FakeRedis
from tests.test_parsers_bilibili import VIDEO_RAW


def _video(bvid: str, title: str) -> dict:
    item = dict(VIDEO_RAW)
    item["bvid"] = bvid
    item["title"] = title
    item["url"] = f"https://www.bilibili.com/video/{bvid}"
    return item


async def _config(session):
    return {"base_url": "http://ai.test", "api_key": "k", "model": "m", "price_in": 0, "price_out": 0}


def _fake_chat(reply: str, usage: dict | None = None):
    async def chat(config, system, user, *, json_mode=False):
        return reply, usage if usage is not None else {"prompt_tokens": 11, "completion_tokens": 7}

    return chat


def _use_ai(monkeypatch, reply: str):
    """把 intent 模块的 AI 依赖换成桩：配置可用 + 固定回复。"""
    monkeypatch.setattr(intent_module, "get_ai_config", _config)
    monkeypatch.setattr(intent_module, "ai_available", lambda config: True)
    monkeypatch.setattr(intent_module, "chat", _fake_chat(reply))


async def _target(session, intent: str = "只在乎官方公告和发货信息") -> CollectTarget:
    target = CollectTarget(
        platform="bilibili",
        target_type="keyword_search",
        target_value="鸣潮",
        intent=intent,
        next_run_at=utcnow(),
    )
    session.add(target)
    await session.commit()
    return target


# ---------- 解析容错 ----------


def test_parse_verdicts_code_fence_and_wrapper():
    reply = '前言说明\n```json\n{"results": [{"id": "a", "tier": "important", "reason": "官宣新版本"}]}\n```'
    verdict = intent_module.parse_verdicts(reply)["a"]
    assert verdict.tier == "important"
    assert verdict.reason == "官宣新版本"


def test_parse_verdicts_bare_list_with_chinese_aliases():
    reply = json.dumps(
        [{"id": "a", "tier": "重要", "reason": "开服"}, {"id": "b", "tier": "忽略", "reason": "灌水"}],
        ensure_ascii=False,
    )
    verdicts = intent_module.parse_verdicts(reply)
    assert verdicts["a"].tier == "important"
    assert verdicts["b"].tier == "ignore"


def test_parse_verdicts_mapping_form():
    reply = json.dumps({"a": "normal", "b": {"tier": "drop", "reason": "无关内容"}})
    verdicts = intent_module.parse_verdicts(reply)
    assert verdicts["a"].tier == "normal"
    assert verdicts["b"].tier == "ignore"


def test_parse_verdicts_garbage_and_illegal_tier():
    assert intent_module.parse_verdicts("完全不是 JSON") == {}
    assert intent_module.normalize_tier(None) == "normal"
    assert intent_module.normalize_tier("") == "normal"
    assert intent_module.normalize_tier("super-urgent") == "normal"
    reply = json.dumps({"results": [{"id": "a", "tier": "随便写", "reason": "x"}]})
    assert intent_module.parse_verdicts(reply)["a"].tier == "normal"


def test_match_verdicts_by_position_and_missing_defaults():
    items = [_video("BV1", "一"), _video("BV2", "二")]
    hits = intent_module.normalize_hits(items, "bilibili_search")
    reply = json.dumps([{"tier": "important"}, {"tier": "ignore"}])
    assert [v.tier for v in intent_module.match_verdicts(reply, hits)] == ["important", "ignore"]
    # 模型漏报：没给结论的条目按 normal 兜底，不丢数据
    assert [v.tier for v in intent_module.match_verdicts(json.dumps({"results": []}), hits)] == [
        "normal",
        "normal",
    ]


def test_reason_is_clamped_to_30_chars():
    reply = json.dumps({"results": [{"id": "a", "tier": "important", "reason": "长" * 60}]})
    assert len(intent_module.parse_verdicts(reply)["a"].reason) == 30


# ---------- fail-open 与用量记账 ----------


async def test_triage_without_intent_skips_ai(session, monkeypatch):
    target = await _target(session, intent=None)
    items = [_video("BV1", "一"), _video("BV2", "二")]

    async def boom(*args, **kwargs):
        raise AssertionError("没配意图不应调用 AI")

    monkeypatch.setattr(intent_module, "chat", boom)
    outcome = await intent_module.triage_hits(session, target, items, "bilibili_search")

    assert outcome.labeled == 0
    assert outcome.ignored == 0
    assert [hit.tier for hit in outcome.hits] == ["", ""]  # 未打标：不写库
    assert await session.scalar(select(func.count()).select_from(AiUsage)) == 0


async def test_triage_fail_open_when_ai_not_configured(session):
    target = await _target(session)
    outcome = await intent_module.triage_hits(
        session, target, [_video("BV1", "一")], "bilibili_search"
    )
    assert outcome.labeled == 1
    assert outcome.ignored == 0
    assert [hit.tier for hit in outcome.hits] == ["normal"]
    assert await session.scalar(select(func.count()).select_from(AiUsage)) == 0


async def test_triage_fail_open_when_chat_fails(session, monkeypatch):
    target = await _target(session)

    async def boom(config, system, user, *, json_mode=False):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(intent_module, "get_ai_config", _config)
    monkeypatch.setattr(intent_module, "ai_available", lambda config: True)
    monkeypatch.setattr(intent_module, "chat", boom)

    outcome = await intent_module.triage_hits(
        session, target, [_video("BV1", "一")], "bilibili_search"
    )
    assert [hit.tier for hit in outcome.hits] == ["normal"]
    assert outcome.ignored == 0
    rows = list((await session.execute(select(AiUsage))).scalars())
    assert [(row.task, row.status) for row in rows] == [("intent_triage", "failed")]


async def test_triage_records_usage_and_drops_ignored(session, monkeypatch):
    target = await _target(session)
    items = [_video("BV1GOOD", "官方公告"), _video("BV2JUNK", "灌水")]
    reply = json.dumps(
        {
            "results": [
                {"id": "BV1GOOD", "tier": "important", "reason": "官方公告"},
                {"id": "BV2JUNK", "tier": "ignore", "reason": "无关灌水"},
            ]
        },
        ensure_ascii=False,
    )
    _use_ai(monkeypatch, reply)

    outcome = await intent_module.triage_hits(session, target, items, "bilibili_search")

    assert outcome.labeled == 2
    assert outcome.ignored == 1
    assert [hit.external_id for hit in outcome.hits] == ["BV1GOOD"]
    assert outcome.important_ids == ["BV1GOOD"]
    row = (await session.execute(select(AiUsage))).scalars().one()
    assert row.task == "intent_triage"
    assert row.status == "ok"
    assert row.ref_id == target.id
    assert row.total_tokens == 18


async def test_persist_tiers_writes_back_and_maps_ids(session, monkeypatch):
    from app.services.ingest import ingest_items

    target = await _target(session)
    reply = json.dumps({"results": [{"id": "BV1", "tier": "important", "reason": "官宣"}]})
    _use_ai(monkeypatch, reply)
    items = [_video("BV1", "一")]
    await ingest_items(session, "bilibili_search", items)
    outcome = await intent_module.triage_hits(session, target, items, "bilibili_search")

    mapping = await intent_module.persist_intent_tiers(session, "bilibili", outcome.hits)

    content_id = await session.scalar(select(RawContent.id).where(RawContent.external_id == "BV1"))
    assert mapping == {"BV1": content_id}
    row = await session.get(RawContent, content_id)
    assert row.intent_tier == "important"
    assert row.intent_reason == "官宣"
    assert row.intent_labeled_at is not None


# ---------- 采集链路三档 ----------


class _StubCollector:
    items: list[dict] = []

    async def fetch(self, target_type, target_value, limit):
        return FetchResult(
            platform="bilibili",
            fetch_kind="bilibili_search",
            items=[dict(item) for item in _StubCollector.items],
        )


async def test_collect_chain_three_tiers(session, monkeypatch, session_factory):
    target = await _target(session)
    bot_target = BotTarget(
        name="我的私聊",
        umo="bot:FriendMessage:self",
        enabled=True,
        config={"updates_enabled": True},
        last_scan_at=utcnow(),
    )
    session.add(bot_target)
    await session.commit()

    _StubCollector.items = [
        _video("BV1GOOD", "官方公告：新版本上线"),
        _video("BV2MID", "玩家二创剪辑"),
        _video("BV3JUNK", "无关灌水"),
    ]
    monkeypatch.setattr(collect_module, "_COLLECTORS", {"bilibili": _StubCollector})
    monkeypatch.setattr(collect_module, "SessionLocal", session_factory)
    _FakeRedis.store.clear()
    reply = json.dumps(
        {
            "results": [
                {"id": "BV1GOOD", "tier": "important", "reason": "官方版本公告"},
                {"id": "BV2MID", "tier": "normal", "reason": "相关二创"},
                {"id": "BV3JUNK", "tier": "ignore", "reason": "无关灌水"},
            ]
        },
        ensure_ascii=False,
    )
    _use_ai(monkeypatch, reply)

    result = await collect_module.run_collection(_FakeRedis(), target.id)

    assert result["status"] == "success"
    assert result["items_ignored"] == 1
    run = await session.get(CollectRun, result["run_id"])
    assert run.items_ignored == 1

    stored = {
        row.external_id: row
        for row in (await session.execute(select(RawContent))).scalars()
    }
    assert set(stored) == {"BV1GOOD", "BV2MID"}  # 判忽略的没有入库
    assert stored["BV1GOOD"].intent_tier == "important"
    assert stored["BV1GOOD"].intent_reason == "官方版本公告"
    assert stored["BV1GOOD"].intent_labeled_at is not None
    assert stored["BV2MID"].intent_tier == "normal"
    assert stored["BV2MID"].intent_labeled_at is not None

    deliveries = list((await session.execute(select(BotDelivery))).scalars())
    assert [row.kind for row in deliveries] == ["critical"]
    assert deliveries[0].target_id == bot_target.id
    assert "新版本上线" in deliveries[0].body


async def test_collect_chain_without_intent_ingests_everything(session, monkeypatch, session_factory):
    target = await _target(session, intent=None)
    _StubCollector.items = [_video("BV1GOOD", "官方公告"), _video("BV2JUNK", "灌水")]
    monkeypatch.setattr(collect_module, "_COLLECTORS", {"bilibili": _StubCollector})
    monkeypatch.setattr(collect_module, "SessionLocal", session_factory)
    _FakeRedis.store.clear()

    async def boom(*args, **kwargs):
        raise AssertionError("没配意图不应调用 AI")

    monkeypatch.setattr(intent_module, "chat", boom)

    result = await collect_module.run_collection(_FakeRedis(), target.id)

    assert result["status"] == "success"
    assert result["items_ignored"] == 0
    assert result["items_new"] == 2
    assert not list((await session.execute(select(BotDelivery))).scalars())
    assert all(
        row.intent_tier is None
        for row in (await session.execute(select(RawContent))).scalars()
    )


# ---------- 机器人守卫 ----------


async def _pushable_content(session, *, external_id: str, title: str, intent_tier=None) -> RawContent:
    now = utcnow()
    raw = RawContent(
        platform="bilibili",
        content_type="video",
        external_id=external_id,
        community="wuwa",
        url=f"https://www.bilibili.com/video/{external_id}",
        published_at=now,
        collected_at=now,
        intent_tier=intent_tier,
    )
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, title=title, text=f"{title}正文"))
    await session.commit()
    return raw


async def _important_event(session, raw: RawContent) -> Event:
    event = Event(
        title=raw.external_id,
        community="wuwa",
        category="update",
        identity="official",
        importance=True,
        conclusion="已官宣",
    )
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id, role="primary"))
    await session.commit()
    return event


async def test_push_important_contents_dedupes_by_content(session):
    target = BotTarget(
        name="我的私聊", umo="bot:FriendMessage:self", enabled=True, config={}, last_scan_at=utcnow()
    )
    session.add(target)
    await session.commit()
    raw = await _pushable_content(session, external_id="BV1", title="官方公告")

    assert await bot_module.push_important_contents(session, [raw.id]) == 1
    assert await bot_module.push_important_contents(session, [raw.id]) == 0  # 按内容去重

    rows = list((await session.execute(select(BotDelivery))).scalars())
    assert len(rows) == 1
    assert rows[0].kind == "critical"
    assert rows[0].status == "pending"  # 发送仍由投递循环负责
    assert rows[0].dedupe_key == bot_module._critical_key(target.id, f"content:{raw.id}")


async def test_push_important_contents_skips_disabled_targets(session):
    target = BotTarget(
        name="停用的", umo="bot:FriendMessage:off", enabled=False, config={}, last_scan_at=utcnow()
    )
    session.add(target)
    await session.commit()
    raw = await _pushable_content(session, external_id="BV1", title="官方公告")

    assert await bot_module.push_important_contents(session, [raw.id]) == 0
    assert not list((await session.execute(select(BotDelivery))).scalars())


async def test_scan_guard_skips_delivered_and_ignored(session, monkeypatch):
    target = BotTarget(
        name="我的私聊",
        umo="bot:FriendMessage:self",
        enabled=True,
        config={"updates_enabled": True},
        last_scan_at=utcnow() - timedelta(minutes=30),
    )
    session.add(target)
    await session.commit()
    pushed = await _pushable_content(session, external_id="BV1PUSHED", title="已直推的公告")
    pushed_event = await _important_event(session, pushed)
    noisy = await _pushable_content(
        session, external_id="BV2NOISE", title="意图判忽略的内容", intent_tier="ignore"
    )
    await _important_event(session, noisy)

    # 采集阶段已直推过这条内容
    assert await bot_module.push_important_contents(session, [pushed.id]) == 1

    await bot_module.run_bot_notifications(session, utcnow())

    rows = list((await session.execute(select(BotDelivery))).scalars())
    assert len(rows) == 1  # 已推送的不重复推；判忽略的不推
    assert rows[0].dedupe_key == bot_module._critical_key(
        target.id, f"event:{pushed_event.id}:{pushed_event.conclusion}"
    )


# ---------- 管理端 API ----------


async def test_admin_target_intent_roundtrip(client, session, admin_headers):
    body = {
        "platform": "bilibili",
        "target_type": "keyword_search",
        "target_value": "鸣潮 意图",
        "intent": "只在乎官方公告和发货信息",
    }
    created = await client.post("/api/admin/collect-targets", headers=admin_headers, json=body)
    assert created.status_code == 201, created.text
    target_id = created.json()["id"]
    assert created.json()["intent"] == "只在乎官方公告和发货信息"

    listed = await client.get("/api/admin/collect-targets", headers=admin_headers)
    assert listed.json()[0]["intent"] == "只在乎官方公告和发货信息"

    updated = await client.put(
        f"/api/admin/collect-targets/{target_id}",
        headers=admin_headers,
        json={"intent": "只在乎联名和抽奖"},
    )
    assert updated.status_code == 200
    assert updated.json()["intent"] == "只在乎联名和抽奖"


async def test_admin_intent_results(client, session, admin_headers):
    target = await _target(session)
    session.add(
        CollectRun(target_id=target.id, status="success", items_found=3, items_ignored=1)
    )
    important = await _pushable_content(session, external_id="BV1", title="重要公告")
    important.intent_tier = "important"
    important.intent_reason = "官方公告"
    important.intent_labeled_at = utcnow() - timedelta(minutes=5)
    normal = await _pushable_content(session, external_id="BV2", title="一般内容")
    normal.intent_tier = "normal"
    normal.intent_labeled_at = utcnow()
    await session.commit()

    result = await client.get("/api/admin/ai/intent-results", headers=admin_headers)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["counts"] == {"important": 1, "normal": 1, "ignored": 1}
    assert [item["tier"] for item in payload["items"]] == ["normal", "important"]
    assert payload["items"][1]["reason"] == "官方公告"
    assert payload["items"][1]["source"] == "bilibili"
    assert payload["items"][1]["labeled_at"] is not None
