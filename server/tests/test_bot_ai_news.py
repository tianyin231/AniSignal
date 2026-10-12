"""快讯按后台分区多选推送；异步审核与旧投递不能绕过订阅范围。"""

from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import BotDelivery, BotTarget, ContentReview, ContentVersion, RawContent, Source
from app.services import bot
from app.services.bot_briefs import build_brief
from tests.test_ai_news import add_video, review
from tests.test_bot import target


async def approved_video(session, monkeypatch):
    raw = await add_video(session)
    await review(session, monkeypatch, [raw.id])
    now = utcnow()
    raw.collected_at = now - timedelta(hours=1)
    row = await session.get(ContentReview, raw.id)
    row.updated_at = now - timedelta(minutes=1)
    await session.commit()
    return raw, now


async def test_ai_queries_keep_reviewed_copy_and_groups_keep_game_scope(session, monkeypatch):
    raw, now = await approved_video(session, monkeypatch)
    private = await target(session, community="ai")
    payload = await bot.query_target(session, private, "news", now=now)
    assert len(payload["items"]) == 1
    assert "作者称部分用户已获测试资格" in payload["text"]
    assert "待核实" in payload["text"] and raw.url in payload["text"]
    assert payload["items"][0]["identity"] != "official"
    private.umo = "bot:GroupMessage:group"
    assert (await bot.query_target(session, private, "news", now=now))["items"]
    private.config = {**private.config, "community": "all", "communities": ["wuwa", "blhx", "hsr", "arknights"]}
    assert not (await bot.query_target(session, private, now=now))["items"]
    source = Source(platform="bilibili", external_id="game", name="游戏官方", identity="official")
    session.add(source)
    await session.flush()
    game = RawContent(platform="bilibili", external_id="game", content_type="video", community="wuwa",
                      source_id=source.id, published_at=now - timedelta(hours=1), collected_at=now - timedelta(minutes=1))
    session.add(game)
    await session.flush()
    session.add(ContentVersion(raw_content_id=game.id, title="鸣潮版本更新", text="新版本开放测试。", category="announcement"))
    await session.commit()
    items = await bot.load_target_candidates(session, private, None, now - timedelta(hours=24), now)
    assert [item["id"] for item in items] == [game.id]


async def test_late_review_pushes_nonofficial_news_once(session, monkeypatch):
    raw, now = await approved_video(session, monkeypatch)
    private = await target(session, community="ai", daily_enabled=False, updates_enabled=True,
                           quiet_start=0, quiet_end=0, merge_minutes=0)
    private.last_scan_at = now - timedelta(minutes=5)
    await session.commit()
    calls = []

    async def send(*args):
        calls.append(args)
        return "accepted", None

    monkeypatch.setattr(bot, "send_message", send)
    await bot.run_bot_notifications(session, now)
    await bot.run_bot_notifications(session, now + timedelta(minutes=1))
    assert len(calls) == 1
    assert calls[0][1] == private.umo and raw.url in calls[0][2]
    row = await session.scalar(select(BotDelivery))
    assert row.kind == "updates" and row.status == "accepted"


async def test_ai_merge_keeps_news_metadata_and_source_link(session, monkeypatch):
    raw, now = await approved_video(session, monkeypatch)
    private = await target(session, community="ai", daily_enabled=False, updates_enabled=True,
                           quiet_start=0, quiet_end=0, merge_minutes=30)
    private.last_scan_at = now - timedelta(minutes=5)
    await session.commit()
    calls = []

    async def send(*args):
        calls.append(args)
        return "accepted", None

    monkeypatch.setattr(bot, "send_message", send)
    await bot.run_bot_notifications(session, now)
    assert not calls
    assert (await bot.load_merge_buffer(session, private.id))["items"][0]["news"]["newsworthy"]
    await bot.run_bot_notifications(session, now + timedelta(minutes=31))
    assert len(calls) == 1 and raw.url in calls[0][2] and "待核实" in calls[0][2]


async def test_old_group_ai_snapshot_and_changed_review_are_cancelled(session, monkeypatch):
    raw, now = await approved_video(session, monkeypatch)
    private = await target(session, community="ai", updates_enabled=True)
    items = await bot.load_target_candidates(session, private, None, now - timedelta(hours=24), now)
    brief = await build_brief(session, items, now)
    group = BotTarget(name="群聊", umo="bot:GroupMessage:group", enabled=True,
                      config={**private.config, "community": "all", "communities": ["wuwa", "hsr"]})
    session.add(group)
    await session.commit()
    blocked = await bot.create_delivery(session, group, "updates", "旧 AI 消息", "old-group-ai", brief=brief)
    assert not await bot.prepare_delivery(session, blocked, target=group, now=now)
    assert blocked.status == "cancelled"
    stale = await bot.create_delivery(session, private, "updates", "旧摘要", "old-private-ai", brief=brief)
    decision = await session.get(ContentReview, raw.id)
    decision.news = {**decision.news, "summary": "作者更正：尚未开放测试。"}
    await session.commit()
    assert not await bot.prepare_delivery(session, stale, target=private, now=now)
    assert stale.status == "cancelled"


async def test_admin_multiselect_is_saved_and_can_enable_ai_for_a_group(client, session, admin_headers, monkeypatch):
    raw, now = await approved_video(session, monkeypatch)
    private = await target(session, community="ai")
    group = BotTarget(name="可选分区的群聊", umo="bot:GroupMessage:group", enabled=True,
                      config={**private.config, "community": "all", "communities": ["wuwa", "hsr"]})
    session.add(group)
    await session.commit()
    overview = (await client.get("/api/admin/bot", headers=admin_headers)).json()
    saved = next(row for row in overview["targets"] if row["id"] == group.id)
    assert saved["communities"] == ["wuwa", "hsr"]
    assert not (await client.post(f"/api/admin/bot/targets/{group.id}/preview", headers=admin_headers,
                                  json={"tool": "news"})).json()["items"]
    saved["communities"] = ["ai"]
    response = await client.put(f"/api/admin/bot/targets/{group.id}", headers=admin_headers, json=saved)
    assert response.status_code == 200
    assert response.json()["community"] == "ai" and response.json()["communities"] == ["ai"]
    preview = await client.post(f"/api/admin/bot/targets/{group.id}/preview", headers=admin_headers, json={"tool": "news"})
    assert [item["id"] for item in preview.json()["items"]] == [raw.id]
    saved["communities"] = []
    assert (await client.put(f"/api/admin/bot/targets/{group.id}", headers=admin_headers, json=saved)).status_code == 422
    saved["communities"] = ["unknown"]
    assert (await client.put(f"/api/admin/bot/targets/{group.id}", headers=admin_headers, json=saved)).status_code == 422


async def test_mixed_subscriptions_keep_reviewed_ai_and_game_items(session, monkeypatch):
    raw, now = await approved_video(session, monkeypatch)
    row = await target(session, communities=["wuwa", "ai"])
    news = (await bot.load_target_candidates(session, row, None, now - timedelta(hours=24), now))[0]

    async def no_ai(*args):
        return {}

    monkeypatch.setattr("app.services.bot_briefs.get_ai_config", no_ai)
    game = {"id": 999, "title": "鸣潮新活动开始", "text": "鸣潮新活动10月15日开启。", "identity": "official",
            "community": "wuwa", "published_at": now - timedelta(minutes=10), "url": "https://example.com/game"}
    brief = await build_brief(session, [game, news], now)
    assert {item["community"] for item in brief["items"]} == {"wuwa", "ai"}
    assert next(item for item in brief["items"] if item["community"] == "ai")["news"] == news["news"]
