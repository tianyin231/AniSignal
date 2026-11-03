"""公开接入的查询成本、详情权限与积压领取回归。"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import BotDelivery, BotTarget
from app.services import bot_briefs
from app.services.account_limits import notification_day
from app.services.settings import get_setting, set_setting
from tests.test_bot_channels import content
from tests.test_bot_companion import HEADERS, setup
from tests.test_bot_preferences import connect, feed
from tests.test_user_bot_tokens import account, issue


@pytest.mark.parametrize("tool", ["digest", "news"])
async def test_manual_queries_return_sources_without_spending_site_ai(client, session, monkeypatch, tool):
    raw = await content(session, "版本更新公告")
    user, key, target = await connect(client, session, communities=["wuwa"])
    await set_setting(session, f"notification_budget_{user.id}", {"day": notification_day(utcnow()), "used": 200})
    config = AsyncMock(side_effect=AssertionError("手动查询不能读取站点模型凭据"))
    model = AsyncMock(side_effect=AssertionError("手动查询不能调用站点模型"))
    monkeypatch.setattr(bot_briefs, "get_ai_config", config)
    monkeypatch.setattr(bot_briefs, "chat", model)
    actor = {"umo": target.umo, "actor_id": "member"}
    query_ids = []
    for _ in range(3):
        response = await client.post("/api/bot/query", headers=key, json={**actor, "tool": tool})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "版本更新公告" in payload["text"]
        assert payload["brief"]["items"][0]["sources"][0]["url"]
        query_ids.append(payload["query_id"])
    config.assert_not_awaited()
    model.assert_not_awaited()
    assert (await get_setting(session, f"notification_budget_{user.id}"))["used"] == 200
    detail = await client.post("/api/bot/detail", headers=key, json={**actor, "query_id": query_ids[-1]})
    assert detail.status_code == 200 and "版本更新公告" in detail.json()["text"]
    assert (await client.post("/api/bot/detail", headers=key, json={**actor, "query_id": query_ids[0]})).status_code == 404
    raw.hidden = True
    await session.commit()
    assert (await client.post("/api/bot/detail", headers=key, json={**actor, "query_id": query_ids[-1]})).status_code == 404


@pytest.mark.parametrize("change", ["disabled", "unsubscribed", "other_target", "other_client", "expired"])
async def test_query_details_recheck_private_monitor_access(client, session, change):
    user, key, target = await connect(client, session, scope_mode="custom")
    private = await feed(session, user.id, "私人监控条目")
    settings = (await client.get("/api/bot/configuration", headers=key)).json()["targets"][0]
    settings["custom_feed_ids"] = [private.id]
    assert (await client.post("/api/bot/configuration", headers=key, json={"targets": [settings]})).status_code == 200
    actor = {"umo": target.umo, "actor_id": "member"}
    query = (await client.post("/api/bot/query", headers=key, json={**actor, "tool": "news"})).json()
    body = {**actor, "query_id": query["query_id"]}
    detail = await client.post("/api/bot/detail", headers=key, json=body)
    assert detail.status_code == 200 and "私人监控条目" in detail.json()["text"]
    if change == "disabled":
        private.enabled = False
        await session.commit()
    elif change == "unsubscribed":
        settings["custom_feed_ids"] = []
        assert (await client.post("/api/bot/configuration", headers=key, json={"targets": [settings]})).status_code == 200
    elif change == "other_target":
        settings["umo"] = "qq:GroupMessage:another"
        assert (await client.post("/api/bot/configuration", headers=key, json={"targets": [settings]})).status_code == 200
        body["umo"] = settings["umo"]
    elif change == "other_client":
        _, cookie = await account(session, "other@example.com")
        _, key = await issue(client, cookie)
        settings["custom_feed_ids"] = []
        assert (await client.post("/api/bot/configuration", headers=key, json={"targets": [settings]})).status_code == 200
    else:
        snapshot = await get_setting(session, f"bot_query_{target.id}")
        await set_setting(session, f"bot_query_{target.id}", {**snapshot, "created_at": (utcnow() - timedelta(days=8)).isoformat()})
    assert (await client.post("/api/bot/detail", headers=key, json=body)).status_code == 404


@pytest.mark.parametrize("blocked_by", ["reminders_disabled", "quiet", "hourly_limit"])
async def test_claim_reaches_other_session_beyond_blocked_first_page(client, session, monkeypatch, blocked_by):
    now = utcnow().replace(hour=16, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.api.bot_companion.utcnow", lambda: now)
    blocked = await setup(session, updates_enabled=True, quiet_start=0, quiet_end=0)
    enabled = BotTarget(name="正常会话", umo="qq:GroupMessage:eligible", enabled=True, config=dict(blocked.config))
    session.add(enabled)
    await session.commit()
    if blocked_by == "reminders_disabled":
        blocked.config = {**blocked.config, "reminders_enabled": False}
    elif blocked_by == "quiet":
        blocked.config = {**blocked.config, "quiet_start": 23, "quiet_end": 8}
    else:
        blocked.config = {**blocked.config, "hourly_limit": 1, "min_push_interval_minutes": 0}
        session.add(BotDelivery(target_id=blocked.id, kind="updates", body="刚发过", status="accepted",
                                dedupe_key="hourly-used", created_at=now, updated_at=now))
    kind = "reminder" if blocked_by == "reminders_disabled" else "updates"
    session.add_all([BotDelivery(target_id=blocked.id, kind=kind, body="暂不可投递", dedupe_key=f"blocked:{i}",
                                created_at=now, updated_at=now) for i in range(101)])
    await session.commit()
    eligible = BotDelivery(target_id=enabled.id, kind="reminder", body="正常提醒", dedupe_key="eligible", created_at=now)
    session.add(eligible)
    await session.commit()
    response = await client.post("/api/bot/outbox/claim", headers=HEADERS, json={})
    assert response.status_code == 200, response.text
    assert response.json()["delivery"]["id"] == eligible.id
    assert response.json()["delivery"]["umo"] == enabled.umo
    pending = list(await session.scalars(select(BotDelivery).where(BotDelivery.target_id == blocked.id, BotDelivery.status == "pending")))
    assert len(pending) == 101 and all(row.attempts == 0 for row in pending)
