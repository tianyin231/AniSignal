"""快讯的准入、时效和来源语义：低播放不拦截，编辑筛选不能冒充事实核查。"""

import asyncio
import json
from datetime import timedelta, timezone

from sqlalchemy import select

from app.clock import utcnow
from app.collectors.bilibili import BilibiliCollector
from app.models import CollectTarget, ContentReview, RawContent
from app.services import curation
from app.services.ai_news import constrain_news, is_news_candidate, parse_news, pending_news_ids, reference_links
from app.services.collect import filter_relevant
from app.services.ingest import ingest_items
from app.services.settings import set_setting
from tests.test_parsers_bilibili import VIDEO_RAW


async def add_video(session, bvid="BVnews", hours=1, **overrides):
    item = dict(VIDEO_RAW, bvid=bvid, title="Codex 新功能开始测试", description="作者报道新功能，仅部分用户可用。",
                view_count=6, owner_mid=999, owner_name="资讯作者",
                pubdate=int((utcnow() - timedelta(hours=hours)).replace(tzinfo=timezone.utc).timestamp()), **overrides)
    await ingest_items(session, "bilibili_search", [item], community="ai")
    return await session.scalar(select(RawContent).where(RawContent.external_id == bvid))


def decision(**overrides):
    return {"relevant": True, "promotional": False, "violation": False, "quality_score": 70,
            "category_hint": "ai_news", "reason": "产品更新报道", "links": [],
            "news": {"newsworthy": True, "headline": "作者报道 Codex 新功能测试",
                     "summary": "作者称部分用户已获测试资格，具体范围待核实。", "kind": "report", "topics": ["Codex"]},
            **overrides}


async def review(session, monkeypatch, ids, result=None):
    await set_setting(session, "ai", {"base_url": "https://example.com/v1", "model": "test", "api_key": "test"})

    async def fake_chat(config, system, user, **kwargs):
        assert "不做没有证据的真实性背书" in system
        return json.dumps({"review": result or decision()}, ensure_ascii=False), None

    monkeypatch.setattr(curation, "chat", fake_chat)
    monkeypatch.setattr(curation, "_cover_data_url", no_cover)
    return await curation.review_contents(session, ids)


async def no_cover(url):
    return None


async def test_newest_search_skips_detail_requests(monkeypatch):
    collector = BilibiliCollector()
    collector.newest_first = True
    calls = []

    async def run(*args):
        calls.append(args)
        return [dict(VIDEO_RAW, title="Codex 新功能开始测试", view_count=6)]

    monkeypatch.setattr(collector, "_run_nonempty", run)
    items = await collector.search_videos("AI 快讯", 15)
    assert items[0]["view_count"] == 6
    assert calls[0][-2:] == ("--order", "pubdate")
    target = CollectTarget(community="ai", target_type="keyword_search", target_value="AI 快讯", min_views=10000)
    assert len(filter_relevant(items, target)[0]) == 1


async def test_ai_news_stays_private_until_reviewed(client, session, monkeypatch):
    raw = await add_video(session)
    candidate = (await curation._review_candidates(session, [raw.id], []))[0]
    assert candidate["cover"] is None
    assert raw.hidden is True
    assert (await client.get("/api/ai-news")).json()["total"] == 0
    assert (await client.get("/api/contents", params={"community": "all"})).json()["total"] == 0
    assert await pending_news_ids(session) == [raw.id]
    await review(session, monkeypatch, [raw.id])
    assert raw.hidden is False
    result = (await client.get("/api/ai-news")).json()
    assert result["total"] == 1
    assert result["items"][0]["verification"] == "pending"
    assert result["items"][0]["source_role"] == "secondary"
    assert await pending_news_ids(session) == []
    detail = (await client.get(f"/api/contents/{raw.id}")).json()
    assert detail["community"] == "ai"
    assert "content_hash" not in detail["news"]


async def test_tutorial_and_missing_news_fields_stay_hidden(session, monkeypatch):
    raw = await add_video(session)
    tutorial = decision()
    tutorial["news"]["newsworthy"] = False
    await review(session, monkeypatch, [raw.id], tutorial)
    assert raw.hidden is True
    assert await pending_news_ids(session) == []
    await review(session, monkeypatch, [raw.id], decision(news=None))
    assert raw.hidden is True
    assert await pending_news_ids(session) == [raw.id]


async def test_news_sorted_by_publication_and_filters_are_literal(client, session, monkeypatch):
    latest = await add_video(session, "BVlatest", hours=1)
    old = await add_video(session, "BVbackfill", hours=48)
    unknown = await add_video(session, "BVunknown", hours=2)
    unknown.published_at = None
    await session.commit()
    await review(session, monkeypatch, [latest.id, old.id, unknown.id])
    rumor = await session.get(ContentReview, latest.id)
    rumor.news = {**rumor.news, "kind": "rumor"}
    await session.commit()
    result = (await client.get("/api/ai-news", params={"topic": "Codex"})).json()
    assert [item["id"] for item in result["items"]] == [latest.id, old.id]
    result = (await client.get("/api/ai-news", params={"hours": 24, "kind": "rumor", "q": "Codex"})).json()
    assert [item["id"] for item in result["items"]] == [latest.id]
    assert (await client.get("/api/ai-news", params={"q": "%"})).json()["total"] == 0
    assert (await client.get("/api/ai-news", params={"topic": "Gemini"})).json()["total"] == 0


async def test_changed_content_requires_fresh_summary(client, session, monkeypatch):
    raw = await add_video(session)
    await review(session, monkeypatch, [raw.id])
    item = dict(raw.raw_json, description="更正：此前消息不实，尚未开始测试。")
    await ingest_items(session, "bilibili_search", [item], community="ai")
    assert raw.hidden is True
    assert raw.id in await pending_news_ids(session)
    # 即使人工临时恢复可见，专用快讯页仍不混用旧摘要与新正文。
    raw.hidden = False
    await session.commit()
    assert (await client.get("/api/ai-news")).json()["total"] == 0


async def test_admin_correction_invalidates_summary(client, session, monkeypatch, admin_headers):
    raw = await add_video(session)
    await review(session, monkeypatch, [raw.id])
    result = await client.patch(f"/api/admin/contents/{raw.id}", headers=admin_headers,
                                json={"text": "更正：还未开始测试。"})
    assert result.status_code == 200
    assert (await client.get("/api/ai-news")).json()["total"] == 0
    assert (await client.get(f"/api/contents/{raw.id}")).json()["news"] is None
    assert raw.id in await pending_news_ids(session)


async def test_source_status_does_not_claim_failed_collection_is_current(client, session):
    session.add(CollectTarget(platform="bilibili", target_type="keyword_search", target_value="AI 快讯",
                              community="ai", enabled=True, last_success_at=utcnow() - timedelta(days=1)))
    await session.commit()
    result = (await client.get("/api/ai-news")).json()
    assert result["sources"][0]["status"] == "delayed"


def test_reference_links_come_from_text_and_news_never_accepts_verified_kind():
    assert reference_links('参考 https://x.com/example/status/123。以及 https://example.com/post?a=1&amp;b=2') == [
        {"url": "https://x.com/example/status/123", "label": "x.com"},
        {"url": "https://example.com/post?a=1&b=2", "label": "example.com"},
    ]
    assert parse_news({"newsworthy": True, "headline": "测试", "summary": "测试", "kind": "verified"}) is None
    value = decision(links=["https://example.com/post", None])
    assert curation._parse_review(json.dumps({"review": value}), 1)["links"] == [
        {"url": "https://example.com/post", "label": ""},
    ]


async def test_review_concurrency_is_shared_across_batches():
    active = peak = 0

    async def batch():
        async def task():
            nonlocal active, peak
            async with curation._shared_review_semaphore():
                active += 1
                peak = max(active, peak)
                await asyncio.sleep(0.01)
                active -= 1
        await asyncio.gather(*(task() for _ in range(3)))

    await asyncio.gather(batch(), batch(), batch())
    assert peak == curation.REVIEW_CONCURRENCY


def test_secondary_news_rejects_demos_and_preserves_future_and_rumor():
    assert not is_news_candidate("一群小肥鱼抢银行")
    assert not is_news_candidate("Claude Code 最新保姆级教程")
    assert is_news_candidate("每日 AI 快讯：Codex 新功能上线")
    news = decision()["news"]
    future = constrain_news({**news, "headline": "智能体发布会举行", "summary": "作者预告新智能体。"},
                            {"title": "新一代本地智能体发布会【预告】", "text": ""})
    assert "预告" in future["headline"]
    rumor = constrain_news(news, {"title": "Codex 新版本疑似泄露", "text": ""})
    assert rumor["kind"] == "rumor"
    leak = constrain_news(news, {"title": "Gemini 开放测试，Claude 新模型泄密曝光", "text": ""})
    assert leak["kind"] == "rumor"
    invented = constrain_news({**news, "summary": "作者报道性能提升 90%。"},
                               {"title": "Codex 新功能测试", "text": "共测试 900 次"})
    assert invented["newsworthy"] is False
