"""资讯内核的业务回放：事件身份、撤稿、请求复用、热度与刊期。"""

import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import AiUsage, ContentReview, ContentVersion, EditorialReceipt, Event, Evidence, NewsGrouping, RawContent, Source
from app.services import editorial_calls
from app.services.editorial_calls import EditorialDeferred, call_json
from app.services.news_editorial import GROUP_PROMPT, SCORE_VERSION, group_pending_news, load_news_records, score_pending_news
from app.services.news_publication import compose_news_edition, heat_at, list_stories, read_news_edition
from app.services.settings import set_setting


async def add_news(session, name, title="Codex 新功能上线", *, hours=2, kind="report", owner=None, official=False, score=85):
    now = utcnow()
    source = Source(platform="x", external_id=name, name=name, identity="official" if official else "user",
        owner_key=owner, first_seen_at=now - timedelta(days=5))
    session.add(source)
    await session.flush()
    raw = RawContent(platform="x", content_type="post", external_id=name, community="ai", source_id=source.id,
        published_at=now - timedelta(hours=hours), url=f"https://x.com/{name}/status/1", content_hash=f"hash-{name}", hidden=False)
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, title=title, text=f"来源宣布{title}，具体范围仍待核实。", category="ai_news"))
    session.add(ContentReview(raw_content_id=raw.id, relevant=True, promotional=False, violation=False, quality_score=score,
        model="test", news={"newsworthy": True, "headline": title, "summary": f"来源报道{title}，具体范围待核实。",
            "kind": kind, "topics": ["Codex"], "content_hash": raw.content_hash, "content_version": 1}))
    await session.commit()
    return raw


async def configure(session, **policy):
    await set_setting(session, "ai", {"base_url": "https://model.example/v1", "api_key": "test", "model": "test"})
    if policy:
        await set_setting(session, "news_editorial", policy)


def model(monkeypatch, relation="SAME_OCCURRENCE", score=80):
    calls = []

    async def chat(config, system, user, **kwargs):
        value = json.loads(user)
        calls.append((system, value))
        if "candidates" in value:
            candidates = value["candidates"]
            result = {"relation": relation, "candidate_id": candidates[0]["id"],
                "related_ids": [c["id"] for c in candidates] if relation == "ROUNDUP" else [], "confidence": 0.96, "reason": "输入有共同发生依据"}
        else:
            result = {"score": score}
        return json.dumps(result, ensure_ascii=False), {"prompt_tokens": 10, "completion_tokens": 5}

    monkeypatch.setattr(editorial_calls, "chat", chat)
    return calls


async def test_same_occurrence_has_multiple_sources_and_stable_identity(session, monkeypatch, client):
    await configure(session)
    first = await add_news(session, "official", hours=4, official=True)
    second = await add_news(session, "reporter", hours=3)
    calls = model(monkeypatch)
    assert (await group_pending_news(session))["grouped"] == 2
    assert len(calls) == 1
    first_group, second_group = await session.get(NewsGrouping, first.id), await session.get(NewsGrouping, second.id)
    assert first_group.event_id == second_group.event_id
    assert (await group_pending_news(session))["grouped"] == 0
    assert len(calls) == 1
    result = (await client.get("/api/ai-news/events")).json()
    assert result["total"] == 1
    assert result["items"][0]["report_count"] == 2
    assert result["items"][0]["id"] == first.id  # 优先官方原稿，不是最新转载。
    assert (await client.get("/api/ai-news")).json()["total"] == 2
    assert (await client.get("/api/events", params={"community": "all"})).json()["total"] == 0


async def test_development_is_another_occurrence_in_same_story(session, monkeypatch):
    await configure(session)
    await add_news(session, "preview", "Codex 新功能预告", hours=5, kind="rumor")
    await group_pending_news(session)
    await add_news(session, "release", "Codex 新功能正式上线", hours=2)
    model(monkeypatch, "SAME_STORY")
    assert (await group_pending_news(session))["grouped"] == 1
    items = await list_stories(session)
    assert len(items) == 1
    assert len(items[0]["developments"]) == 2
    assert items[0]["developments"][0]["kind"] == "report"
    assert items[0]["developments"][1]["kind"] == "rumor"
    assert await session.scalar(select(func.count(Event.id))) == 2


async def test_new_news_is_grouped_and_scored_before_historical_backlog(session, monkeypatch):
    await configure(session)
    old = await add_news(session, "backlog", "Codex 较早的独立研究", hours=100)
    latest = await add_news(session, "latest", "Codex 最新功能上线", hours=1, official=True)
    model(monkeypatch, "UNRELATED")
    assert (await group_pending_news(session, limit=1))["grouped"] == 1
    assert await session.get(NewsGrouping, latest.id) is not None
    assert await session.get(NewsGrouping, old.id) is None
    await group_pending_news(session, limit=1)
    assert (await score_pending_news(session, limit=1))["scored"] == 1
    assert "selection" in (await session.get(ContentReview, latest.id)).news
    assert "selection" not in (await session.get(ContentReview, old.id)).news


async def test_changed_review_invalidates_grouping_without_changing_original_version(session, monkeypatch):
    await configure(session)
    await add_news(session, "one", hours=4)
    second = await add_news(session, "two", hours=3)
    model(monkeypatch)
    await group_pending_news(session)
    assert len(await list_stories(session)) == 1
    review = await session.get(ContentReview, second.id)
    review.news = {**review.news, "headline": "Codex 另一个独立更新", "summary": "来源报道 Codex 独立变化。"}
    await session.commit()
    assert len(await list_stories(session)) == 2
    model(monkeypatch, "UNRELATED")
    assert (await group_pending_news(session))["grouped"] == 1
    assert len(await list_stories(session)) == 2


async def test_roundup_cannot_bridge_independent_stories(session, monkeypatch):
    await configure(session)
    await add_news(session, "one", "Codex 发布新功能", hours=5)
    await group_pending_news(session)
    await add_news(session, "two", "Codex 公布独立研究", hours=4)
    model(monkeypatch, "UNRELATED")
    await group_pending_news(session)
    roundup = await add_news(session, "roundup", "Codex 新功能与研究日报", hours=3)
    model(monkeypatch, "ROUNDUP")
    await group_pending_news(session)
    grouping = await session.get(NewsGrouping, roundup.id)
    assert grouping.relation == "ROUNDUP"
    assert grouping.event_id is None
    assert len(grouping.related_event_ids) == 2
    assert len(await list_stories(session)) == 2


async def test_withdrawn_representative_and_revision_cannot_leak_old_copy(session, monkeypatch, client):
    await configure(session)
    first = await add_news(session, "one", "Codex 原始发布", hours=4, official=True)
    await add_news(session, "two", "Codex 发布的媒体报道", hours=3)
    model(monkeypatch)
    await group_pending_news(session)
    first.hidden = True
    await session.commit()
    result = (await client.get("/api/ai-news/events")).json()
    assert result["items"][0]["report_count"] == 1
    assert "原始发布" not in result["items"][0]["headline"]
    first.hidden, first.content_hash = False, "new-hash"
    session.add(ContentVersion(raw_content_id=first.id, version=2, title="更正", text="此前说法已更正", category="ai_news"))
    await session.commit()
    result = (await client.get("/api/ai-news/events")).json()
    assert result["total"] == 1  # 旧审核不匹配新版，不能临时恢复可见就放行。
    assert result["items"][0]["sources"][0]["id"] != first.id


async def test_two_score_calls_are_independent_and_reused(session, monkeypatch):
    await configure(session)
    await add_news(session, "one", official=True)
    calls = model(monkeypatch, score=70)
    await group_pending_news(session)
    assert (await score_pending_news(session))["scored"] == 1
    assert len(calls) == 2
    assert (await list_stories(session, selected=True))[0]["score"] == 70
    assert (await score_pending_news(session))["scored"] == 0
    review = await session.scalar(select(ContentReview))
    review.news = {k: v for k, v in review.news.items() if k != "selection"}
    await session.commit()
    assert (await score_pending_news(session))["scored"] == 1
    assert len(calls) == 2  # 业务状态丢失后，从持久回执恢复两次评分。
    assert await session.scalar(select(func.count(AiUsage.id))) == 2


async def test_budget_stops_second_independent_request(session, monkeypatch):
    await configure(session, calls_per_day=1)
    calls = model(monkeypatch)
    assert await call_json(session, task="news_score", ref_id=1, system="score", payload={}) == {"score": 80}
    with pytest.raises(EditorialDeferred, match="额度"):
        await call_json(session, task="news_score", ref_id=1, system="score", payload={}, round_id=2)
    assert len(calls) == 1


async def test_transport_uncertainty_waits_for_explicit_release(session, monkeypatch):
    await configure(session)
    calls = 0

    async def timeout(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("response was not received")

    monkeypatch.setattr(editorial_calls, "chat", timeout)
    for _ in range(2):
        with pytest.raises(EditorialDeferred):
            await call_json(session, task="news_group", ref_id=1, system=GROUP_PROMPT, payload={})
    assert calls == 1
    assert (await session.scalar(select(EditorialReceipt))).status == "unknown"


async def test_malformed_relation_never_merges_and_response_is_reused(session, monkeypatch):
    await configure(session)
    await add_news(session, "one", hours=4)
    await group_pending_news(session)
    second = await add_news(session, "two", hours=3)
    calls = 0

    async def invalid(*args, **kwargs):
        nonlocal calls
        calls += 1
        return json.dumps({"relation": "SAME_OCCURRENCE", "candidate_id": 999, "confidence": 0.99}), None

    monkeypatch.setattr(editorial_calls, "chat", invalid)
    assert (await group_pending_news(session))["deferred"] == 1
    assert await session.get(NewsGrouping, second.id) is None
    await group_pending_news(session)
    assert calls == 1
    assert len(await list_stories(session)) == 2


async def test_model_result_is_rejected_if_input_changed_during_request(session, monkeypatch):
    await configure(session)
    await add_news(session, "one", hours=4)
    await group_pending_news(session)
    second = await add_news(session, "two", hours=3)

    second_id = second.id

    async def changed(config, system, user, **kwargs):
        payload = json.loads(user)
        second.content_hash = "changed"
        session.add(ContentVersion(raw_content_id=second.id, version=2, title="Codex 更正", text="更正", category="ai_news"))
        review = await session.get(ContentReview, second.id)
        review.news = {**review.news, "content_hash": "changed", "content_version": 2}
        await session.commit()
        return json.dumps({"relation": "SAME_OCCURRENCE", "candidate_id": payload["candidates"][0]["id"], "confidence": 0.99}), None

    monkeypatch.setattr(editorial_calls, "chat", changed)
    assert (await group_pending_news(session))["deferred"] == 1
    assert await session.get(NewsGrouping, second_id) is None


async def test_manual_lock_and_excluded_evidence_are_preserved(session, monkeypatch):
    await configure(session)
    first = await add_news(session, "one", hours=4)
    await group_pending_news(session)
    assignment = await session.get(NewsGrouping, first.id)
    event = await session.get(Event, assignment.event_id)
    event.manual_locked, event.title = True, "人工编辑的标题"
    first.content_hash = "new"
    session.add(ContentVersion(raw_content_id=first.id, version=2, title="Codex 修订", text="修订", category="ai_news"))
    review = await session.get(ContentReview, first.id)
    review.news = {**review.news, "content_hash": "new", "content_version": 2}
    await session.commit()
    assert (await group_pending_news(session))["grouped"] == 1
    assert event.title == "人工编辑的标题"
    evidence = await session.scalar(select(Evidence).where(Evidence.raw_content_id == first.id))
    evidence.role = "excluded"
    await session.commit()
    assert await list_stories(session) == []


async def test_heat_counts_owner_once_and_decays_by_publication(session):
    await add_news(session, "one", hours=24, owner="acme")
    await add_news(session, "two", hours=24, owner="acme")
    rows = await load_news_records(session)
    now = utcnow()
    heat, participants = heat_at(rows, now)
    assert participants == 1
    assert heat == 5.0
    for row in rows:
        row.raw.collected_at = now + timedelta(days=1)
    assert heat_at(rows, now) == (heat, participants)
    assert heat_at(rows, now + timedelta(hours=25)) == (0, 0)
    for row in rows:
        row.source = None
    assert heat_at(rows, now) == (0, 0)  # 无法识别作者时，不虚构独立来源。


async def test_filter_and_pagination_are_applied_to_stories(session, monkeypatch, client):
    await configure(session)
    await add_news(session, "one", "Codex 发布甲功能", hours=4)
    await add_news(session, "two", "Codex 发布乙功能", hours=3)
    model(monkeypatch, "UNRELATED")
    await group_pending_news(session)
    response = await client.get("/api/ai-news/events", params={"limit": 1})
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1
    assert (await client.get("/api/ai-news/events", params={"q": "%"})).json()["total"] == 0
    assert (await client.get("/api/ai-news/events", params={"q": "乙功能"})).json()["total"] == 1


async def test_fixed_edition_reuses_snapshot_and_withdraws_dependent_copy(session, monkeypatch):
    await configure(session)
    raw = await add_news(session, "one", official=True)
    await group_pending_news(session)
    model(monkeypatch)
    await score_pending_news(session)
    now = datetime.combine(utcnow().date(), datetime.min.time()) + timedelta(hours=1)  # 北京时间 09:00
    end = now.replace(hour=23, minute=50) - timedelta(days=1)
    raw.published_at = end - timedelta(hours=2)
    review = await session.get(ContentReview, raw.id)
    review.updated_at = end - timedelta(hours=1)
    review.news = {**review.news, "selection": {**review.news["selection"], "scored_at": (end - timedelta(minutes=30)).isoformat()}}
    grouping = await session.get(NewsGrouping, raw.id)
    grouping.created_at = end - timedelta(hours=1)
    await session.commit()
    issue = await compose_news_edition(session, now=now)
    assert len(issue.content["items"]) == 1
    assert (await compose_news_edition(session, now=now)).id == issue.id
    assert len((await read_news_edition(session))["items"]) == 1
    raw.hidden = True
    await session.commit()
    assert (await read_news_edition(session))["items"] == []


async def test_late_scoring_moves_to_next_edition_instead_of_being_lost(session, monkeypatch):
    await configure(session)
    raw = await add_news(session, "one", official=True)
    await group_pending_news(session)
    model(monkeypatch)
    await score_pending_news(session)
    now = datetime.combine(utcnow().date(), datetime.min.time()) + timedelta(hours=1)
    end = now.replace(hour=23, minute=50) - timedelta(days=1)
    raw.published_at = end - timedelta(hours=2)
    review = await session.get(ContentReview, raw.id)
    review.updated_at = end - timedelta(hours=1)
    review.news = {**review.news, "selection": {**review.news["selection"], "scored_at": (end + timedelta(minutes=1)).isoformat()}}
    grouping = await session.get(NewsGrouping, raw.id)
    grouping.created_at = end - timedelta(hours=1)
    await session.commit()
    assert await compose_news_edition(session, now=now) is None
    issue = await compose_news_edition(session, now=now + timedelta(days=1))
    assert len(issue.content["items"]) == 1


async def test_admin_policy_validation_and_receipt_release(session, monkeypatch, client, admin_headers):
    await configure(session)
    assert (await client.get("/api/admin/ai/editorial")).status_code in {401, 403, 422}
    assert (await client.put("/api/admin/ai/editorial", headers=admin_headers, json={"calls_per_day": -1})).status_code == 422
    assert (await client.put("/api/admin/ai/editorial", headers=admin_headers, json={"calls_per_day": 17})).status_code == 200
    assert (await client.get("/api/admin/ai/editorial", headers=admin_headers)).json()["policy"]["calls_per_day"] == 17
    receipt = EditorialReceipt(request_key="retry-test", task="news_group", ref_id=1, model="test", prompt_version="v1", status="unknown")
    session.add(receipt)
    await session.commit()
    url = f"/api/admin/ai/editorial/receipts/{receipt.id}/retry"
    assert (await client.post(url, headers=admin_headers, json={})).status_code == 400
    assert (await client.post(url, headers=admin_headers, json={"confirmed": True})).status_code == 200
    await session.refresh(receipt)
    assert receipt.status == "failed"


async def test_scoring_budget_defers_multiple_records_without_expired_session_errors(session, monkeypatch):
    await configure(session)
    await add_news(session, "one", hours=4)
    await add_news(session, "two", hours=3)
    model(monkeypatch, "UNRELATED")
    await group_pending_news(session)
    await set_setting(session, "news_editorial", {"calls_per_day": 0})
    assert (await score_pending_news(session))["deferred"] == 2


async def test_manual_merge_and_split_update_news_membership(session, monkeypatch, client, admin_headers):
    await configure(session)
    first = await add_news(session, "one", hours=4)
    second = await add_news(session, "two", hours=3)
    model(monkeypatch, "UNRELATED")
    await group_pending_news(session)
    a, b = await session.get(NewsGrouping, first.id), await session.get(NewsGrouping, second.id)
    target_id = b.event_id
    response = await client.post("/api/admin/events/merge", headers=admin_headers,
        json={"source_id": a.event_id, "target_id": target_id})
    assert response.status_code == 200
    assert (await client.get("/api/ai-news/events")).json()["total"] == 1
    response = await client.post(f"/api/admin/events/{target_id}/split", headers=admin_headers,
        json={"content_ids": [first.id]})
    assert response.status_code == 200
    result = (await client.get("/api/ai-news/events")).json()
    assert result["total"] == 2
    assert sorted(item["report_count"] for item in result["items"]) == [1, 1]


async def test_bot_and_rss_reuse_event_selection(session, monkeypatch, client):
    from app.services.ai_news import load_bot_news

    await configure(session)
    await add_news(session, "one", hours=4, owner="acme")
    await add_news(session, "two", hours=3, owner="other")
    model(monkeypatch)
    await group_pending_news(session)
    await score_pending_news(session)
    items = await load_bot_news(session, None, utcnow() - timedelta(days=1), utcnow())
    assert len(items) == 1
    assert len(items[0]["content_ids"]) == 2
    assert items[0]["event_id"] is not None
    assert len(await list_stories(session, sort="hot")) == 1
    feed = await client.get("/api/ai-news/rss.xml")
    assert feed.status_code == 200
    assert feed.text.count("<item>") == 1
    assert "one" in feed.text and "two" in feed.text


def test_migration_preserves_existing_sources_and_compiles_for_mysql():
    import importlib.util
    import io
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / "alembic/versions/0042_news_editorial.py"
    spec = importlib.util.spec_from_file_location("news_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        for table in ("sources", "raw_contents", "events", "ai_usage"):
            connection.execute(sa.text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO sources (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text("SELECT id, owner_key, editorial_tier FROM sources")).one() == (1, None, None)
            assert "news_groupings" in sa.inspect(connection).get_table_names()
            migration.downgrade()
        assert connection.execute(sa.text("SELECT id FROM sources")).scalar() == 1
    output = io.StringIO()
    with Operations.context(MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": output})):
        migration.upgrade()
    assert "CREATE TABLE news_groupings" in output.getvalue()
    assert "FOREIGN KEY(story_id) REFERENCES events" in output.getvalue()
