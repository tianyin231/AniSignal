"""模型冷却可恢复；国服、国际服的同一发布物按会话去重。"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import AiUsage, BotDelivery, ContentMatch, ContentVersion, RawContent, Source
from app.services import bot, editorial_calls, similarity
from app.services.ai import AiPoolCoolingDown
from app.services.editorial_calls import EditorialDeferred, call_json
from tests.test_bot_policy import target
from tests.test_news_editorial import configure


@pytest.mark.parametrize("legacy", [False, True])
async def test_local_cooldown_resumes_without_spending_request_budget(session, monkeypatch, legacy):
    await configure(session, calls_per_day=1)
    now = utcnow()
    monkeypatch.setattr(editorial_calls, "utcnow", lambda: now)
    model = AsyncMock(side_effect=AiPoolCoolingDown("本地冷却，未发送请求"))
    monkeypatch.setattr(editorial_calls, "chat", model)
    with pytest.raises(EditorialDeferred):
        await call_json(session, task="news_score", ref_id=1, system="score", payload={})
    receipt = await session.scalar(select(editorial_calls.EditorialReceipt))
    usage = await session.get(AiUsage, receipt.usage_id)
    assert (receipt.status, receipt.attempts, usage.status) == ("deferred", 0, "deferred")
    if legacy:
        receipt.status, receipt.attempts, usage.status = "unknown", 1, "unknown"
        await session.commit()
    with pytest.raises(EditorialDeferred):
        await call_json(session, task="news_score", ref_id=1, system="score", payload={})
    assert model.await_count == 1
    now += timedelta(seconds=61)
    model.side_effect = None
    model.return_value = ('{"score":80}', {})
    assert await call_json(session, task="news_score", ref_id=1, system="score", payload={}) == {"score": 80}
    assert receipt.status == "received" and receipt.attempts == 1
    assert model.await_count == 2


@pytest.fixture
async def regional_releases(session):
    now = utcnow()
    items = []
    for platform, title, text in [
        ("kuro_cn", "清宵角色音乐《尘外客》现已发布", "清宵角色音乐现已发布，欢迎收听。"),
        ("youtube", 'Qingxiao Theme Song: Outside the Mountain', 'Qingxiao theme song is now available.'),
    ]:
        source = Source(platform=platform, external_id=platform, name=title, identity="official")
        session.add(source)
        await session.flush()
        raw = RawContent(platform=platform, content_type="video", external_id=platform, community="wuwa",
                         source_id=source.id, published_at=now - timedelta(hours=1), url=f"https://example.com/{platform}")
        session.add(raw)
        await session.flush()
        session.add(ContentVersion(raw_content_id=raw.id, title=title, text=text, parsed_at=now - timedelta(minutes=2)))
        items.append({"id": raw.id, "title": title, "text": text, "platform": platform, "identity": "official",
                      "community": "wuwa", "published_at": raw.published_at, "url": raw.url, "strength": "strong", "keywords": []})
    session.add(ContentMatch(left_raw_id=items[0]["id"], right_raw_id=items[1]["id"], is_same_event=True,
                             confidence=.98, model="saved-match", updated_at=now - timedelta(minutes=1)))
    await session.commit()
    return now, items


@pytest.mark.parametrize("status", ["pending", "accepted", "unknown"])
async def test_confirmed_translation_does_not_push_again_in_same_group(session, regional_releases, status):
    now, items = regional_releases
    row = await target(session, now)
    original = await bot.create_delivery(session, row, "updates", "国服原文", "regional-old", brief=bot.single_item_brief(items[0]))
    original.status, original.updated_at = status, now
    await session.commit()
    known = await bot.known_notification_keys(session, row.id, now)
    assert await bot.prepare_update_items(session, row.id, [items[1]], now, known) == []
    duplicate = await bot.create_delivery(session, row, "updates", "国际服译文", "regional-new", brief=bot.single_item_brief(items[1]))
    assert not await bot.prepare_delivery(session, duplicate, target=row, now=now)
    assert duplicate.status == "cancelled"


async def test_same_batch_and_daily_keep_one_release_but_other_group_can_receive_it(session, regional_releases):
    now, items = regional_releases
    row = await target(session, now)
    prepared = await bot.prepare_update_items(session, row.id, items, now, set())
    assert len(prepared) == 1
    collapsed = await bot._collapse_releases(session, items)
    assert len(collapsed) == 1 and set(collapsed[0]["content_ids"]) == {item["id"] for item in items}
    other = await target(session, now, umo="qq:GroupMessage:another-region")
    assert len(await bot.prepare_update_items(session, other.id, [items[1]], now, set())) == 1


@pytest.mark.parametrize("change", ["new_revision", "low_confidence", "different_community"])
async def test_revision_or_unconfirmed_relation_does_not_hide_real_updates(session, regional_releases, change):
    now, items = regional_releases
    row = await target(session, now)
    previous = await bot.create_delivery(session, row, "updates", "原公告", "regional-prior", brief=bot.single_item_brief(items[0]))
    previous.status, previous.updated_at = "accepted", now
    if change == "new_revision":
        session.add(ContentVersion(raw_content_id=items[1]["id"], version=2, title=items[1]["title"],
                                   text="额外新增奖励100星声。", parsed_at=now))
        items[1] = {**items[1], "text": "额外新增奖励100星声。"}
    elif change == "low_confidence":
        (await session.scalar(select(ContentMatch))).confidence = .6
    else:
        (await session.get(RawContent, items[1]["id"])).community = "hsr"
        items[1] = {**items[1], "community": "hsr"}
    await session.commit()
    known = await bot.known_notification_keys(session, row.id, now)
    assert len(await bot.prepare_update_items(session, row.id, [items[1]], now, known)) == 1


def test_same_platform_regional_accounts_can_be_matched_but_same_account_is_not_batched():
    now = utcnow()
    first = similarity.MatchContent(1, "youtube", now, "国服公告", "原文", "official_announce", source_id=11)
    translated = similarity.MatchContent(2, "youtube", now, "Global announcement", "Translation", "official_announce", source_id=12)
    next_post = similarity.MatchContent(3, "youtube", now, "另一条公告", "不同内容", "official_announce", source_id=11)
    pairs = {pair.key for pair in similarity._candidate_pairs([first, translated, next_post], None)}
    assert (1, 2) in pairs and (1, 3) not in pairs


async def test_published_report_and_bot_edition_follow_confirmed_event_merges(session, session_factory, monkeypatch):
    from app.api.admin import EventMergeRequest, merge_events
    from app.config import get_settings
    from app.models import NewsEdition, NewsGrouping
    from app.services import aihot, native_news, news_reports
    from app.services.editorial_calls import fingerprint
    from app.services.ai_news import news_review_identity
    from app.services.news_editorial import group_pending_news, load_news_records
    from tests.test_news_editorial import add_news, model

    await configure(session)
    first = await add_news(session, "report-a", "纽约时报起诉Brave浏览器", hours=4)
    second = await add_news(session, "report-b", "纽约时报等媒体起诉Brave", hours=3)
    model(monkeypatch, "UNRELATED")
    await group_pending_news(session)
    records = await load_news_records(session)
    items = [{"id": r.raw.id, "headline": r.news["headline"], "summary": r.news["summary"],
              "source_name": r.source.name, "sources": [], "url": r.raw.url,
              "source_versions": {str(r.raw.id): [r.raw.content_hash, r.version.version, fingerprint(news_review_identity(r.news))]}}
             for r in records]
    day = utcnow().date()
    issue = NewsEdition(community="ai", day=day, content={"items": items}, rule_version="test")
    session.add(issue)
    await session.commit()
    assert len((await news_reports.report(session, "daily", str(day)))["sections"][0]["items"]) == 2
    first_group, second_group = await session.get(NewsGrouping, first.id), await session.get(NewsGrouping, second.id)
    await merge_events(EventMergeRequest(source_id=second_group.event_id, target_id=first_group.event_id), session=session)
    report = await news_reports.report(session, "daily", str(day))
    assert len(report["sections"][0]["items"]) == 1
    assert len(issue.content["items"]) == 2  # 只修正展示，不重写已出刊的原始快照。
    monkeypatch.setattr(get_settings(), "news_unified_enabled", True)
    monkeypatch.setattr(native_news, "SessionLocal", session_factory)
    assert len((await aihot.edition(day))["items"]) == 1


def test_contradictory_group_decision_is_not_applied():
    from app.services.news_editorial import parse_relation
    with pytest.raises(ValueError, match="结论与理由矛盾"):
        parse_relation({"relation": "UNRELATED", "candidate_id": None, "confidence": .98,
                        "reason": "两篇报道属于同一次诉讼，因此应归为SAME_OCCURRENANCE。"}, [])
