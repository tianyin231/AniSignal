import json
from datetime import timedelta

import pytest

from app.clock import utcnow
from app.models import BotDelivery
from app.services import bot, bot_news
from app.services.bot_briefs import build_shared_brief, format_brief
from app.services.settings import set_setting
from tests.test_bot import target
from tests.test_bot_ai_news import approved_video


async def enqueue(session, target_row, headline, summary, *, raw_id=1, status="pending", checked=False, kind="updates"):
    now = utcnow()
    news = {"newsworthy": True, "headline": headline, "summary": summary, "kind": "report", "topics": ["行业动态"]}
    item = {"id": raw_id, "news": news, "community": "ai", "source": "消息来源", "platform": "x",
            "published_at": now - timedelta(minutes=5), "url": f"https://x.com/test/status/{raw_id}"}
    brief = await build_shared_brief(session, [item], now, mode="daily" if kind == "daily" else "updates")
    if checked:
        brief["news_dedupe_checked"] = True
    row = BotDelivery(target_id=target_row.id, kind=kind, dedupe_key=f"test:{target_row.id}:{raw_id}",
                      body=format_brief(brief), brief=brief, status=status, created_at=now, updated_at=now)
    session.add(row)
    await session.commit()
    return row


async def model(session, monkeypatch, decisions, seen):
    await set_setting(session, "ai", {"base_url": "https://example.com/v1", "model": "test", "api_key": "test"})

    async def fake_chat(config, system, user, **kwargs):
        seen.append(json.loads(user))
        return json.dumps({"items": decisions}, ensure_ascii=False), None

    monkeypatch.setattr(bot_news, "chat", fake_chat)


async def test_cross_source_duplicates_removed_but_recovery_and_new_version_kept(session, monkeypatch):
    t = await target(session, community="ai")
    await enqueue(session, t, "DeepSeek 服务中断", "来源报道 DeepSeek 服务发生故障。", status="accepted")
    duplicate = await enqueue(session, t, "DeepSeek 网页及 APP 暂时不可用", "作者称服务故障仍未恢复。", raw_id=2)
    recovery = await enqueue(session, t, "DeepSeek 服务恢复", "来源报道 DeepSeek 服务恢复。", raw_id=3)
    new_version = await enqueue(session, t, "DeepSeek 新模型发布", "来源报道 DeepSeek 新模型发布。", raw_id=4)
    seen = []
    await model(session, monkeypatch, [
        {"index": 0, "action": "skip", "covered_by": ["history:0"]},
        {"index": 1, "action": "keep"}, {"index": 2, "action": "keep"},
    ], seen)
    result = await bot_news.dedupe_pending_news(session, t.id, utcnow())
    assert result == {"checked": 3, "cancelled": 1}
    assert duplicate.status == "cancelled" and duplicate.brief["items"][0]["id"] == 2
    assert recovery.status == new_version.status == "pending"
    assert recovery.brief["news_dedupe_checked"] and new_version.brief["news_dedupe_checked"]
    assert seen[0]["history"][0]["headline"] == "DeepSeek 服务中断"
    assert (await bot_news.dedupe_pending_news(session, t.id, utcnow()))["checked"] == 0
    assert len(seen) == 1


async def test_compilation_only_keeps_new_fact_without_replacing_original_review(session, monkeypatch):
    t = await target(session, community="ai")
    await enqueue(session, t, "DeepSeek 服务恢复", "来源报道服务恢复。", status="unknown")
    row = await enqueue(session, t, "DeepSeek 恢复并补偿", "来源报道服务恢复，并退还 7 天费用。", raw_id=2, kind="daily")
    original = row.brief["items"][0]["news"].copy()
    seen = []
    await model(session, monkeypatch, [{"index": 0, "action": "update", "covered_by": ["history:0"],
        "headline": "DeepSeek 公布补偿", "new_facts": ["退还 7 天费用"]}], seen)
    await bot_news.dedupe_pending_news(session, t.id, utcnow())
    assert "恢复" not in row.body and "退还 7 天费用" in row.body
    assert "https://x.com/test/status/2" in row.body and "待核实" in row.body
    assert row.brief["items"][0]["news"] == original
    assert row.brief["items"][0]["notification_news"]["summary"] == "来源补充称：退还 7 天费用"


async def test_pending_deduped_news_counts_as_history_but_other_target_and_cancelled_do_not(session, monkeypatch):
    t = await target(session, community="ai")
    await enqueue(session, t, "Anthropic 发布 74 项更新", "来源报道 52 天更新 74 项。", checked=True)
    await enqueue(session, t, "已取消的新闻", "来源报道其他变化。", raw_id=2, status="cancelled")
    other = type(t)(name="另一个会话", umo="bot:FriendMessage:other", enabled=True, config=t.config)
    session.add(other)
    await session.commit()
    await enqueue(session, other, "其他会话的新闻", "来源报道其他变化。", status="accepted")
    row = await enqueue(session, t, "Anthropic 52天发布74项更新", "作者转述该更新。", raw_id=3)
    seen = []
    await model(session, monkeypatch, [{"index": 0, "action": "skip", "covered_by": ["history:0"]}], seen)
    await bot_news.dedupe_pending_news(session, t.id, utcnow())
    assert row.status == "cancelled" and len(seen[0]["history"]) == 1


@pytest.mark.parametrize("decision", [
    {"index": 0, "action": "skip", "covered_by": []},
    {"index": 0, "action": "skip", "covered_by": ["candidate:0"]},
    {"index": 0, "action": "update", "covered_by": ["history:0"], "headline": "退款", "new_facts": ["退还 90 天费用"]},
    {"index": 0, "action": "update", "covered_by": ["history:0"], "headline": "退还90天", "new_facts": ["退还 7 天费用"]},
])
async def test_invalid_dedupe_response_stays_pending_and_backs_off(session, monkeypatch, decision):
    t = await target(session, community="ai")
    await enqueue(session, t, "DeepSeek 服务恢复", "来源报道服务恢复。", status="accepted")
    row = await enqueue(session, t, "DeepSeek 公布补偿", "来源报道退还 7 天费用。", raw_id=2)
    seen = []
    await model(session, monkeypatch, [decision], seen)
    await bot_news.dedupe_pending_news(session, t.id, utcnow())
    await bot_news.dedupe_pending_news(session, t.id, utcnow() + timedelta(minutes=1))
    assert row.status == "pending" and not row.brief.get("news_dedupe_checked")
    assert row.error and len(seen) == 1


async def test_claim_waits_for_news_dedupe_and_no_history_passes_without_another_model(session, monkeypatch):
    raw, now = await approved_video(session, monkeypatch)
    t = await target(session, community="ai", updates_enabled=True, quiet_start=0, quiet_end=0)
    items = await bot.load_target_candidates(session, t, None, now - timedelta(days=1), now)
    brief = await build_shared_brief(session, items, now)
    row = await bot.create_delivery(session, t, "updates", format_brief(brief), "claim-news", brief=brief)
    assert not await bot.prepare_delivery(session, row, target=t, now=now)
    assert row.status == "pending" and row.attempts == 0
    assert (await bot_news.dedupe_pending_news(session, t.id, now))["checked"] == 1
    assert await bot.prepare_delivery(session, row, target=t, now=now)
