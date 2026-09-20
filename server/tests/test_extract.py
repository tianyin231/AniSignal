"""官方内容实体抽取：解析健壮性、官方门禁、去重、推送与延期标记。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.clock import utcnow
from app.models import BotDelivery, BotTarget, ContentSchedule, ContentVersion, RawContent, RedeemCode, Source
from app.services import extract as extract_module
from app.services.extract import _parse_extraction, extract_entities, sweep_extractions
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import VIDEO_RAW
from tests.test_parsers_others import KURO_ITEM


def test_parse_extraction_tolerates_fences_and_garbage():
    raw = '''```json
{"schedules": [
    {"title": "限时活动「深海回响」", "start_at": "2026-09-12T11:00:00+08:00", "end_at": null, "kind": "activity"},
    {"title": "坏数据", "start_at": "not-a-time", "kind": "activity"},
    {"title": "未知类型", "start_at": "2026-09-12T11:00:00+08:00", "kind": "party"}
 ],
 "codes": [{"code": "WUWA2026", "expires_at": null}, {"code": "x", "expires_at": null}]}
```'''
    schedules, codes = _parse_extraction(raw)
    assert schedules == [
        {
            "title": "限时活动「深海回响」",
            "kind": "activity",
            "start_at": datetime(2026, 9, 12, 3, 0),
            "end_at": None,
            "all_day": False,
        }
    ]
    assert codes == [{"code": "WUWA2026", "expires_at": None}]
    assert _parse_extraction("完全不是 JSON") == ([], [])


async def _seed_official(session):
    await ingest_items(session, "kuro_notices", [KURO_ITEM])
    await session.commit()
    content_id = (
        await session.execute(select(RawContent.id).where(RawContent.external_id == "50553"))
    ).scalar_one()
    version = await session.scalar(select(ContentVersion).where(ContentVersion.raw_content_id == content_id))
    version.text = "限时活动：2026年10月1日11:00至2026年10月15日18:00。兑换码 WUWA2026。"
    await session.commit()
    return content_id


def _mock_ai(payload: str):
    chat = AsyncMock(return_value=(payload, None))
    usage = AsyncMock(return_value=None)
    return chat, usage


def _fake_config():
    return {"base_url": "http://x", "api_key": "k", "model": "test-model"}


async def test_extract_entities_skips_non_official(client, session, monkeypatch):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    await session.commit()
    content_id = (
        await session.execute(select(RawContent.id).where(RawContent.external_id == "BV1Mcgp6ME1x"))
    ).scalar_one()
    fake_chat, fake_usage = _mock_ai("{}")
    monkeypatch.setattr(extract_module, "chat", fake_chat)
    monkeypatch.setattr(extract_module, "record_usage", fake_usage)
    monkeypatch.setattr(extract_module, "get_ai_config", AsyncMock(return_value=_fake_config()))
    result = await extract_entities(session, content_id)
    assert result == {"status": "skipped", "reason": "not_official"}
    fake_chat.assert_not_called()


async def test_extract_entities_schedules_codes_and_push(client, session, monkeypatch):
    content_id = await _seed_official(session)
    session.add(
        BotTarget(
            name="推群",
            umo="qq:GroupMessage:media",
            enabled=True,
            config={"updates_enabled": True, "community": "wuwa", "delivery_mode": "astrbot"},
        )
    )
    await session.commit()

    payload = (
        '{"schedules": [{"title": "限时活动「深海回响」", "start_at": "2026-10-01T11:00:00+08:00", '
        '"end_at": "2026-10-15T18:00:00+08:00", "kind": "activity", "date_evidence": "2026年10月1日11:00至2026年10月15日18:00"}], '
        '"codes": [{"code": "WUWA2026", "expires_at": null}]}'
    )
    fake_chat, fake_usage = _mock_ai(payload)
    monkeypatch.setattr(extract_module, "chat", fake_chat)
    monkeypatch.setattr(extract_module, "record_usage", fake_usage)
    monkeypatch.setattr(extract_module, "get_ai_config", AsyncMock(return_value=_fake_config()))

    result = await extract_entities(session, content_id)
    assert result["status"] == "ok" and result["schedules"] == 1 and result["codes"] == 1

    schedule = (await session.execute(select(ContentSchedule))).scalar_one()
    assert schedule.title == "限时活动「深海回响」"
    assert schedule.kind == "activity"
    assert schedule.start_at == datetime(2026, 10, 1, 3, 0)

    code = (await session.execute(select(RedeemCode))).scalar_one()
    assert code.code == "WUWA2026" and code.status == "active"

    delivery = (await session.execute(select(BotDelivery))).scalar_one()
    assert delivery.dedupe_key == "code:wuwa:WUWA2026"
    assert "WUWA2026" in delivery.body

    # 重复抽取不新增（extracted_at 已置位 + 业务唯一键兜底）
    raw = await session.get(RawContent, content_id)
    assert raw.extracted_at is not None
    result = await extract_entities(session, content_id)
    assert result["schedules"] == 0 and result["codes"] == 0


async def test_delay_notice_marks_maintenance_and_pushes(client, session, monkeypatch):
    content_id = await _seed_official(session)
    session.add(
        BotTarget(
            name="推群",
            umo="qq:GroupMessage:media",
            enabled=True,
            config={"updates_enabled": True, "community": "all", "delivery_mode": "astrbot"},
        )
    )
    session.add(
        ContentSchedule(
            raw_content_id=content_id,
            community="wuwa",
            title="版本更新停机维护",
            kind="maintenance",
            start_at=utcnow() + timedelta(hours=1),
            end_at=utcnow() + timedelta(hours=7),
            created_at=utcnow(),
        )
    )
    await session.commit()

    delayed_start = (utcnow() + timedelta(hours=2)).isoformat() + "Z"
    version = await session.scalar(select(ContentVersion).where(ContentVersion.raw_content_id == content_id))
    version.text = f"版本更新停机维护延迟到 {delayed_start}。具体内容请参考后续公告。"
    session.add(ContentSchedule(raw_content_id=content_id, community="wuwa", title="另一个活动", kind="activity", start_at=utcnow(), end_at=utcnow() + timedelta(days=3)))
    await session.commit()
    payload = (
        '{"schedules": [{"title": "版本更新停机维护（延期）", "start_at": "' + delayed_start + '", '
        '"end_at": null, "kind": "delay", "date_evidence": "' + delayed_start + '"}], "codes": []}'
    )
    fake_chat, fake_usage = _mock_ai(payload)
    monkeypatch.setattr(extract_module, "chat", fake_chat)
    monkeypatch.setattr(extract_module, "record_usage", fake_usage)
    monkeypatch.setattr(extract_module, "get_ai_config", AsyncMock(return_value=_fake_config()))

    await extract_entities(session, content_id)
    maintenance = (await session.execute(select(ContentSchedule).where(ContentSchedule.kind == "maintenance"))).scalar_one()
    assert maintenance.delayed is True
    unrelated = await session.scalar(select(ContentSchedule).where(ContentSchedule.title == "另一个活动"))
    assert unrelated.delayed is False
    delivery = (await session.execute(select(BotDelivery).where(BotDelivery.kind == "critical"))).scalar_one()
    assert "日程变更" in delivery.body


async def test_sweep_extracts_pending_official_only(client, session, monkeypatch):
    content_id = await _seed_official(session)
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    await session.commit()

    fake_chat, fake_usage = _mock_ai('{"schedules": [], "codes": []}')
    monkeypatch.setattr(extract_module, "chat", fake_chat)
    monkeypatch.setattr(extract_module, "record_usage", fake_usage)
    monkeypatch.setattr(extract_module, "get_ai_config", AsyncMock(return_value=_fake_config()))

    result = await sweep_extractions(session)
    # 只有官方内容进入抽取；非官方内容不被触碰
    assert result["candidates"] == 1
    assert fake_chat.await_count == 1
    official_raw = await session.get(RawContent, content_id)
    assert official_raw.extracted_at is not None
    non_official = (
        await session.execute(select(RawContent).where(RawContent.external_id == "BV1Mcgp6ME1x"))
    ).scalar_one()
    assert non_official.extracted_at is None

    # 再扫一轮：无待抽取内容
    assert (await sweep_extractions(session))["candidates"] == 0


async def test_calendar_groups_by_status_and_codes_hide_expired(client, session):
    content_id = await _seed_official(session)
    now = utcnow()
    session.add_all(
        [
            ContentSchedule(
                raw_content_id=content_id, community="wuwa", title="进行中的活动",
                kind="activity", start_at=now - timedelta(days=1), end_at=now + timedelta(days=3),
                created_at=now,
            ),
            ContentSchedule(
                raw_content_id=content_id, community="wuwa", title="即将开始的维护",
                kind="maintenance", start_at=now + timedelta(hours=5), end_at=now + timedelta(hours=11),
                created_at=now,
            ),
            ContentSchedule(
                raw_content_id=content_id, community="wuwa", title="已结束的活动",
                kind="activity", start_at=now - timedelta(days=5), end_at=now - timedelta(days=2),
                created_at=now,
            ),
            RedeemCode(
                raw_content_id=content_id, community="wuwa", code="LIVE2026",
                status="active", expires_at=now + timedelta(days=7), created_at=now,
            ),
            RedeemCode(
                raw_content_id=content_id, community="wuwa", code="OLDCODE1",
                status="active", expires_at=now - timedelta(days=1), created_at=now,
            ),
        ]
    )
    await session.commit()

    calendar = (await client.get("/api/calendar", params={"community": "wuwa"})).json()
    assert [e["title"] for e in calendar["ongoing"]] == ["进行中的活动"]
    assert [e["title"] for e in calendar["upcoming"]] == ["即将开始的维护"]
    assert [e["title"] for e in calendar["ended"]] == ["已结束的活动"]
    assert calendar["ongoing"][0]["url"]

    codes = (await client.get("/api/codes", params={"community": "wuwa"})).json()
    assert [c["code"] for c in codes["items"]] == ["LIVE2026"]


def test_schedule_dates_require_source_and_publication_anchor():
    import json

    def parse(start, evidence="今晚19:00", end=None):
        return _parse_extraction(json.dumps({"schedules": [{"title": "前瞻直播", "kind": "broadcast", "start_at": start, "end_at": end, "date_evidence": evidence}]}), source_text="前瞻直播将于今晚19:00开始。", published_at=datetime(2026, 9, 19, 4))[0]

    assert not parse("2026-01-01T19:00:00+08:00")
    assert not parse("2026-09-19T19:00:00+08:00", "9月19日19:00")  # 不能伪造原文
    assert not parse("2026-09-19T19:00:00+08:00", end="2026-09-18T20:00:00+08:00")
    assert parse("2026-09-19T19:00:00+08:00")[0]["start_at"] == datetime(2026, 9, 19, 11)


async def test_calendar_unknown_end_deduplication_and_hidden_source(client, session):
    content_id = await _seed_official(session)
    now = utcnow()
    session.add_all([
        ContentSchedule(raw_content_id=content_id, community="wuwa", title="几个月前的直播", kind="broadcast", start_at=now - timedelta(days=90)),
        ContentSchedule(raw_content_id=content_id, community="wuwa", title="刚开播", kind="broadcast", start_at=now - timedelta(hours=1)),
        ContentSchedule(raw_content_id=content_id, community="wuwa", title="刚结束", kind="activity", start_at=now - timedelta(days=2), end_at=now - timedelta(hours=1)),
        ContentSchedule(raw_content_id=content_id, community="wuwa", title="3.7版本更新", kind="maintenance", start_at=now + timedelta(days=2)),
        ContentSchedule(raw_content_id=content_id, community="wuwa", title="Version 3.7 Update", kind="maintenance", start_at=now + timedelta(days=2)),
        ContentSchedule(raw_content_id=content_id, community="wuwa", title="3.7版本角色试用", kind="activity", start_at=now + timedelta(days=2)),
    ])
    await session.commit()
    data = (await client.get("/api/calendar")).json()
    assert data["ongoing"] == []
    assert [e["title"] for e in data["recent"]] == ["刚开播"]
    assert [e["title"] for e in data["ended"]] == ["刚结束"]
    assert {e["title"] for e in data["upcoming"]} == {"3.7版本更新", "3.7版本角色试用"}
    (await session.get(RawContent, content_id)).hidden = True
    await session.commit()
    assert all(not rows for rows in (await client.get("/api/calendar")).json().values())
