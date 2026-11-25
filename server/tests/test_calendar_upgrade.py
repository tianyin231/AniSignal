"""活动日历升级单测：跨月时间轴 /api/calendar/range 与个人打卡 /api/account/calendar-state。"""

from datetime import datetime

from sqlalchemy import select

from app.models import ContentSchedule, EmailCode, RawContent
from app.services.ingest import ingest_items
from tests.test_parsers_bilibili import VIDEO_RAW


async def _register_and_login(client, session, email="cal@t.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "password123"})
    code_row = await session.scalar(select(EmailCode).order_by(EmailCode.id.desc()).limit(1))
    resp = await client.post("/api/auth/verify", json={"email": email, "code": code_row.code_plain})
    assert resp.status_code == 200
    return resp


async def test_calendar_range_spans_months_and_filters(client, session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    content_id = (await session.execute(select(RawContent.id))).scalar_one()

    session.add_all([
        ContentSchedule(
            raw_content_id=content_id,
            community="wuwa",
            title="跨月卡池",
            kind="pool",
            start_at=datetime(2026, 10, 20, 2, 0),
            end_at=datetime(2026, 11, 10, 4, 0),
        ),
        ContentSchedule(
            raw_content_id=content_id,
            community="hsr",
            title="十一月前瞻直播",
            kind="broadcast",
            start_at=datetime(2026, 11, 5, 11, 30),
            end_at=None,
        ),
        ContentSchedule(
            raw_content_id=content_id,
            community="wuwa",
            title="窗口外旧活动",
            kind="activity",
            start_at=datetime(2026, 9, 1, 2, 0),
            end_at=datetime(2026, 9, 15, 2, 0),
        ),
        ContentSchedule(
            raw_content_id=content_id,
            community="wuwa",
            title="窗口外未来活动",
            kind="activity",
            start_at=datetime(2026, 12, 20, 2, 0),
            end_at=datetime(2026, 12, 30, 2, 0),
        ),
    ])
    await session.commit()

    resp = await client.get(
        "/api/calendar/range",
        params={"community": "all", "start": "2026-10-25", "days": 30},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["start"] == "2026-10-25"
    assert body["end"] == "2026-11-23"
    assert body["days"] == 30
    assert {item["title"] for item in body["items"]} == {"跨月卡池", "十一月前瞻直播"}

    # 单分区过滤
    wuwa = await client.get(
        "/api/calendar/range",
        params={"community": "wuwa", "start": "2026-10-25", "days": 30},
    )
    assert [item["title"] for item in wuwa.json()["items"]] == ["跨月卡池"]

    # 隐藏来源内容后立即不可见
    (await session.get(RawContent, content_id)).hidden = True
    await session.commit()
    hidden_resp = await client.get(
        "/api/calendar/range",
        params={"community": "all", "start": "2026-10-25", "days": 30},
    )
    assert hidden_resp.json()["items"] == []


async def test_calendar_range_validates_params(client):
    assert (await client.get("/api/calendar/range", params={"start": "2026-02-31"})).status_code == 400
    assert (await client.get("/api/calendar/range", params={"start": "not-a-date"})).status_code == 422
    assert (await client.get("/api/calendar/range", params={"days": 3})).status_code == 422
    assert (await client.get("/api/calendar/range", params={"days": 120})).status_code == 422


async def test_account_calendar_state_sync_and_coexistence(client, session):
    # 未登录必须 401
    assert (await client.get("/api/account/calendar-state")).status_code == 401
    assert (await client.put("/api/account/calendar-state", json={"timezone": "Asia/Tokyo"})).status_code == 401

    await _register_and_login(client, session)

    # 默认空状态 + 北京时间
    init = await client.get("/api/account/calendar-state")
    assert init.status_code == 200
    assert init.json() == {
        "claimed_codes": [],
        "completed_schedules": [],
        "timezone": "Asia/Shanghai",
    }

    # 写入打卡与时区（自动去重与去空白）
    saved = await client.put(
        "/api/account/calendar-state",
        json={
            "claimed_codes": ["wuwa:CODE1", "wuwa:CODE1", " hsr:STAR99 "],
            "completed_schedules": [101, 202, 101],
            "timezone": "Asia/Tokyo",
        },
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "claimed_codes": ["wuwa:CODE1", "hsr:STAR99"],
        "completed_schedules": [101, 202],
        "timezone": "Asia/Tokyo",
    }

    # 局部更新仅改时区，保留已领兑换码与已完成日程
    partial = await client.put(
        "/api/account/calendar-state",
        json={"timezone": "America/New_York"},
    )
    assert partial.status_code == 200
    assert partial.json() == {
        "claimed_codes": ["wuwa:CODE1", "hsr:STAR99"],
        "completed_schedules": [101, 202],
        "timezone": "America/New_York",
    }

    # 更新关注流偏好与屏蔽分区不得覆盖 calendar_state
    assert (
        await client.put(
            "/api/account/feed-preferences",
            json={"communities": ["wuwa"], "exclude_keywords": ["二创"]},
        )
    ).status_code == 200
    assert (
        await client.put(
            "/api/account/hidden-communities",
            json={"communities": ["blhx"]},
        )
    ).status_code == 200

    after = await client.get("/api/account/calendar-state")
    assert after.status_code == 200
    assert after.json() == {
        "claimed_codes": ["wuwa:CODE1", "hsr:STAR99"],
        "completed_schedules": [101, 202],
        "timezone": "America/New_York",
    }

    # 参数校验边界
    assert (await client.put("/api/account/calendar-state", json={"timezone": "Mars/Olympus"})).status_code == 400
    assert (await client.put("/api/account/calendar-state", json={"completed_schedules": [True]})).status_code == 400
    assert (await client.put("/api/account/calendar-state", json={"completed_schedules": [-1]})).status_code == 400
    assert (await client.put("/api/account/calendar-state", json={"claimed_codes": ["x" * 81]})).status_code == 400
    assert (await client.put("/api/account/calendar-state", json={"claimed_codes": "not-list"})).status_code == 400
