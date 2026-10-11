"""官方内容实体抽取：解析健壮性、官方门禁、去重、推送与延期标记。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import AiUsage, BotDelivery, BotTarget, ContentSchedule, ContentVersion, RawContent, RedeemCode, Source
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


@pytest.mark.parametrize('published_missing', [False, True])
async def test_extract_entities_schedules_codes_and_push(client, session, monkeypatch, published_missing):
    content_id = await _seed_official(session)
    raw = await session.get(RawContent, content_id)
    if published_missing:
        raw.published_at = None
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
    assert delivery.dedupe_key == f"code:wuwa:WUWA2026:target:{delivery.target_id}"
    assert "WUWA2026" in delivery.body
    assert delivery.brief['items'][0]['sources'][0]['published_at'] == (raw.published_at.isoformat() if raw.published_at else None)

    # 重复抽取不新增（extracted_at 已置位 + 业务唯一键兜底）
    raw = await session.get(RawContent, content_id)
    assert raw.extracted_at is not None
    result = await extract_entities(session, content_id)
    assert result["schedules"] == 0 and result["codes"] == 0


@pytest.mark.parametrize('published_missing', [False, True])
async def test_delay_notice_marks_maintenance_and_pushes(client, session, monkeypatch, published_missing):
    content_id = await _seed_official(session)
    raw = await session.get(RawContent, content_id)
    if published_missing:
        raw.published_at = None
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
    assert delivery.brief['items'][0]['sources'][0]['published_at'] == (raw.published_at.isoformat() if raw.published_at else None)


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


@pytest.mark.parametrize("failure", [TimeoutError("upstream timeout"), "not JSON"])
async def test_extraction_failure_is_retried_after_cooldown(session, monkeypatch, failure):
    content_id = await _seed_official(session)
    raw = await session.get(RawContent, content_id)
    chat = AsyncMock(side_effect=[failure if isinstance(failure, Exception) else (failure, None),
                                 ('{"schedules": [], "codes": []}', None)])
    monkeypatch.setattr(extract_module, "chat", chat)
    monkeypatch.setattr(extract_module, "get_ai_config", AsyncMock(return_value=_fake_config()))
    assert (await sweep_extractions(session))["failed"] == 1
    assert raw.extracted_at is None
    assert (await sweep_extractions(session))["candidates"] == 0
    monkeypatch.setattr("app.services.ai.utcnow", lambda: utcnow() + timedelta(minutes=11))
    assert (await sweep_extractions(session))["candidates"] == 1
    assert raw.extracted_at is not None
    assert chat.await_count == 2


async def test_extraction_stops_after_three_failures(session, monkeypatch):
    content_id = await _seed_official(session)
    version = await session.scalar(select(ContentVersion).where(ContentVersion.raw_content_id == content_id))
    version.parsed_at = utcnow() - timedelta(hours=2)
    session.add_all([AiUsage(task="extract_entities", ref_id=content_id, model="test", status="failed",
                             created_at=utcnow() - timedelta(hours=1)) for _ in range(3)])
    await session.commit()
    chat = AsyncMock()
    monkeypatch.setattr(extract_module, "chat", chat)
    assert (await sweep_extractions(session))["candidates"] == 0
    chat.assert_not_awaited()


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
    from app.services.extract import _date_supported

    def parse(start, evidence="今晚19:00", end=None):
        return _parse_extraction(json.dumps({"schedules": [{"title": "前瞻直播", "kind": "broadcast", "start_at": start, "end_at": end, "date_evidence": evidence}]}), source_text="前瞻直播将于今晚19:00开始。", published_at=datetime(2026, 9, 19, 4))[0]

    assert not parse("2026-01-01T19:00:00+08:00")
    assert not parse("2026-09-19T19:00:00+08:00", "9月19日19:00")  # 不能伪造原文
    assert not parse("2026-09-19T19:00:00+08:00", end="2026-09-18T20:00:00+08:00")
    assert parse("2026-09-19T19:00:00+08:00")[0]["start_at"] == datetime(2026, 9, 19, 11)
    assert not _date_supported("2030-09-19T19:00:00+08:00", "9月19日19:00", datetime(2026, 9, 19))
    assert not _date_supported("2026-03-07T00:00:00+08:00", "3.7版本开启", datetime(2026, 9, 19))
    assert not _date_supported("2026-09-19T00:00:00+08:00", "September 19, 2025", datetime(2026, 9, 19))
    assert _date_supported("2026-09-30T00:00:00+08:00", "September 30th (UTC+8)", datetime(2026, 9, 19))
    days = _parse_extraction(json.dumps({"schedules": [{"title": "展会", "kind": "activity", "all_day": True,
        "start_at": "2026-10-02T00:00:00+08:00", "end_at": "2026-10-05T00:00:00+08:00",
        "date_evidence": "10月2日-10月5日"}]}), source_text="展会日期：10月2日-10月5日", published_at=datetime(2026, 9, 19))[0]
    assert days[0]["end_at"] == datetime(2026, 10, 5, 15, 59, 59)


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


async def test_calendar_export_utc_all_day_and_visibility(client, session):
    content_id = await _seed_official(session)
    schedule = ContentSchedule(raw_content_id=content_id, community="wuwa", title="活动,报名;说明\n请看原文" * 8,
                               kind="activity", start_at=datetime(2026, 10, 1, 3), end_at=datetime(2026, 10, 1, 8))
    session.add(schedule)
    await session.commit()
    response = await client.get(f"/api/calendar/{schedule.id}.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert 'attachment;' in response.headers["content-disposition"]
    assert b"DTSTART:20261001T030000Z\r\n" in response.content
    assert b"DTEND:20261001T080000Z\r\n" in response.content
    assert all(len(line) <= 75 for line in response.content.split(b"\r\n"))
    unfolded = response.text.replace("\r\n ", "")
    assert "活动\\,报名\\;说明\\n请看原文" in unfolded
    schedule.all_day = True
    schedule.start_at = datetime(2026, 9, 30, 16)
    schedule.end_at = None
    await session.commit()
    response = await client.get(f"/api/calendar/{schedule.id}.ics")
    assert "DTSTART;VALUE=DATE:20261001\r\n" in response.text
    assert "DTEND" not in response.text
    (await session.get(RawContent, content_id)).hidden = True
    await session.commit()
    assert (await client.get(f"/api/calendar/{schedule.id}.ics")).status_code == 404


async def test_month_calendar_includes_full_grid_history_and_cross_month_events(client, session):
    content_id = await _seed_official(session)
    # 2024 年 2 月的六周月历为 1 月 29 日至 3 月 10 日（北京时间）。
    rows = [
        ("跨月活动", datetime(2024, 1, 1), datetime(2024, 2, 2)),
        ("左边界", datetime(2024, 1, 28, 16), None),
        ("左边界之前", datetime(2024, 1, 28, 15, 59), None),
        ("右边界内", datetime(2024, 3, 10, 15, 59), None),
        ("右边界之外", datetime(2024, 3, 10, 16), None),
        ("无结束时间的旧活动", datetime(2024, 1, 20), None),
        ("闰日直播", datetime(2024, 2, 29, 12), None),
    ]
    session.add_all([
        ContentSchedule(raw_content_id=content_id, community="wuwa", title=title,
                        kind="activity", start_at=start, end_at=end)
        for title, start, end in rows
    ])
    # 不沿用首页每组 5 / 10 条的截断，否则月历会漏掉同日活动。
    session.add_all([
        ContentSchedule(raw_content_id=content_id, community="wuwa", title=f"活动 {i}",
                        kind="activity", start_at=datetime(2024, 2, 10, 2))
        for i in range(12)
    ])
    session.add(ContentSchedule(raw_content_id=content_id, community="hsr", title="其他游戏",
                                kind="broadcast", start_at=datetime(2024, 2, 10, 3)))
    await session.commit()

    response = await client.get("/api/calendar/month", params={"month": "2024-02", "community": "wuwa"})
    assert response.status_code == 200
    data = response.json()
    assert data["month"] == "2024-02"
    assert {entry["title"] for entry in data["items"]} == {
        "跨月活动", "左边界", "右边界内", "闰日直播", *(f"活动 {i}" for i in range(12)),
    }
    all_games = (await client.get("/api/calendar/month", params={"month": "2024-02", "community": "all"})).json()
    assert len(all_games["items"]) == 17
    (await session.get(RawContent, content_id)).hidden = True
    await session.commit()
    assert (await client.get("/api/calendar/month", params={"month": "2024-02"})).json()["items"] == []


async def test_month_calendar_rejects_invalid_months(client):
    for month in ("2026-00", "2026-13", "2026-2", "not-a-month", "9999-12"):
        assert (await client.get("/api/calendar/month", params={"month": month})).status_code == 422
