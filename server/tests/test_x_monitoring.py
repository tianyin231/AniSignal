"""X 账号配置、引用归属、采集入库和资讯审核闭环。"""

from datetime import timedelta, timezone
from html import escape
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.collectors import x as x_module
from app.collectors.base import CollectorDeferred, CollectorError
from app.models import BotTarget, CollectTarget, ContentVersion, RawContent
from app.parsers.x import normalize_account, parse_page, parse_post
from app.services import collect, curation
from app.services.ai_news import constrain_news
from app.services.bot import load_target_candidates
from app.services.ingest import ingest_items, reparse_contents
from app.services.queue import LocalRedis
from tests.test_ai_news import decision, review


def entry(number="100", text="Codex now supports a new API.", author="OpenAI", stamp=None, quote=""):
    stamp = stamp or int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    return (f'<div data-timeline-entry="" data-href="/{author}/status/{number}"><article>'
            f'<div dir="auto" class="whitespace-pre-wrap">{escape(text)}</div>{quote}'
            f'<script>formatRelativePastTimestamp({{"timestamp":{stamp * 1000}}})</script></article></div>')


def test_account_and_post_preserve_original_author_time_and_media():
    assert normalize_account("https://x.com/OpenAI/") == "openai"
    assert normalize_account("@OpenAI") == "openai"
    quote = entry("99", "A different Claude update.", "someone_else", 1700000000)
    media = '<img src="https://pbs.twimg.com/media/test.jpg"><img src="https://pbs.twimg.com/profile_images/avatar.jpg">'
    rows = parse_page(entry(quote=quote + media, stamp=1700000300))
    assert len(rows) == 1
    post = parse_post(rows[0])
    assert post.text == "Codex now supports a new API."
    assert post.author.external_id == "openai" and post.author.identity == "official"
    assert post.published_at.timestamp() == 1700000300
    assert post.media == ["https://pbs.twimg.com/media/test.jpg"]
    assert post.extra["quoted_urls"] == ["https://x.com/someone_else/status/99"]


def test_profile_without_timeline_marker_keeps_quotes_separate():
    quote = entry("99", "Quoted news must not replace the original.", "someone_else", 1700000000)
    page = entry(text="Original post.", stamp=1700000300, quote=quote)
    page = page.replace('data-timeline-entry=""', 'role="link" data-navigation-stop=""')
    rows = parse_page(page)
    assert len(rows) == 1
    assert rows[0]["author"] == "OpenAI" and rows[0]["text"] == "Original post."
    assert rows[0]["published_at"] == "2023-11-14T22:18:20+00:00"
    assert rows[0]["quoted_urls"] == ["https://x.com/someone_else/status/99"]


async def test_admin_can_create_edit_pause_and_trigger_x_account(client, admin_headers, fake_queue):
    response = await client.post("/api/admin/collect-targets", headers=admin_headers, json={
        "platform": "x", "target_type": "account_dynamics", "target_value": "https://x.com/OpenAI",
        "community": "ai", "interval_minutes": 60,
    })
    assert response.status_code == 201
    row = response.json()
    assert row["target_value"] == "openai" and row["min_views"] == 0
    path = f"/api/admin/collect-targets/{row['id']}"
    duplicate = await client.post("/api/admin/collect-targets", headers=admin_headers, json={
        "platform": "x", "target_type": "account_dynamics", "target_value": "@OPENAI", "interval_minutes": 60,
    })
    assert duplicate.status_code == 409
    edited = await client.put(path, headers=admin_headers, json={
        "target_value": "@bcherny", "display_name": "Boris", "interval_minutes": 120, "enabled": False,
    })
    assert edited.status_code == 200
    assert edited.json()["target_value"] == "bcherny" and edited.json()["interval_minutes"] == 120
    assert edited.json()["enabled"] is False
    assert (await client.put(path, headers=admin_headers, json={"interval_minutes": 5})).status_code == 422
    assert (await client.put(path, headers=admin_headers, json={"target_value": "https://x.com/openai/status/123"})).status_code == 422
    assert (await client.post(path + "/run", headers=admin_headers)).status_code == 200
    assert fake_queue.enqueued == [row["id"]]


async def test_collector_fills_long_post_and_does_not_retry_login(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    def handle(request):
        requests.append(request.url.path)
        assert "cookie" not in request.headers
        if request.url.path == "/openai":
            return httpx.Response(200, text=entry(text="Codex now supports Show more"))
        return httpx.Response(200, text='<meta property="og:url" content="https://x.com/OpenAI/status/100">'
            '<article><div dir="auto" class="whitespace-pre-wrap">Codex now supports a new API.</div>'
            '<script>formatFullTimestamp({"timestamp":1700000300000})</script></article>')

    monkeypatch.setattr(x_module.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    async def no_sleep(_):
        pass
    monkeypatch.setattr(x_module.asyncio, "sleep", no_sleep)
    result = await x_module.XCollector().fetch("account_dynamics", "@OpenAI", 20)
    assert result.items[0]["text"] == "Codex now supports a new API."
    assert result.items[0]["truncated"] is False
    assert requests == ["/openai", "/OpenAI/status/100"]
    for status, text in [(403, "refused"), (200, "<html>empty</html>")]:
        monkeypatch.setattr(x_module.httpx, "AsyncClient", lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, text=text)), **kwargs))
        with pytest.raises(CollectorError):
            await x_module.XCollector().fetch("account_dynamics", "openai", 20)


async def test_x_collection_ingests_english_posts_and_queues_review(session, session_factory, monkeypatch):
    class Redis(LocalRedis):
        def __init__(self):
            super().__init__()
            self.jobs = []

        async def enqueue_job(self, name, ids, **kwargs):
            self.jobs.append((name, ids))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(x_module.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=entry())), **kwargs))
    monkeypatch.setattr(collect, "SessionLocal", session_factory)
    monkeypatch.setattr(x_module, "_REQUEST_INTERVAL_RANGE_SECONDS", (0.001, 0.001))
    target = CollectTarget(platform="x", target_type="account_dynamics", target_value="openai",
                           community="ai", interval_minutes=60, enabled=True)
    session.add(target)
    await session.commit()
    redis = Redis()
    result = await collect.run_collection(redis, target.id)
    assert result["status"] == "success" and result["items_new"] == 1
    raw = await session.scalar(select(RawContent).where(RawContent.platform == "x"))
    assert raw.hidden is True and raw.community == "ai"
    assert redis.jobs == [("review_contents", [raw.id])]
    assert (await collect.run_collection(redis, target.id))["items_new"] == 0
    assert await session.scalar(select(func.count()).select_from(RawContent)) == 1


async def test_x_news_review_publication_and_bot_query(client, session, monkeypatch, admin_headers):
    raw_item = parse_page(entry())[0]
    await ingest_items(session, "x_posts", [raw_item], community="ai")
    raw = await session.scalar(select(RawContent).where(RawContent.platform == "x"))
    candidate = (await curation._review_candidates(session, [raw.id], []))[0]
    assert candidate["text_truncated"] is False
    assert (await client.get("/api/ai-news")).json()["total"] == 0
    await review(session, monkeypatch, [raw.id], decision(news={
        "newsworthy": True, "headline": "Codex 新增 API 支持", "summary": "OpenAI 原帖宣布 Codex 新增 API 支持。",
        "kind": "report", "topics": ["Codex"],
    }))
    response = (await client.get("/api/ai-news")).json()
    assert response["total"] == 1 and response["items"][0]["source_role"] == "original"
    bot_target = BotTarget(name="AI", umo="test:FriendMessage:1", config={"communities": ["ai"]})
    now = utcnow() + timedelta(seconds=1)
    assert len(await load_target_candidates(session, bot_target, None, now - timedelta(days=1), now)) == 1
    checked = await reparse_contents(session, "x")
    assert checked["checked"] == 1 and checked["failed"] == 0
    assert (await client.post("/api/admin/reparse", headers=admin_headers)).status_code == 200
    assert constrain_news(decision()["news"], {**candidate, "text_truncated": True})["newsworthy"] is False


async def test_full_x_text_survives_later_profile_excerpt(session):
    short = parse_page(entry(text="Codex now supports Show more"))[0]
    full = {**short, "text": "Codex now supports a new API.", "truncated": False, "text_scope": "full_post"}
    await ingest_items(session, "x_posts", [short], community="ai")
    await ingest_items(session, "x_posts", [{**short, "text": "", "truncated": False}], community="ai")
    await ingest_items(session, "x_posts", [full], community="ai")
    await ingest_items(session, "x_posts", [short], community="ai")
    latest = await session.scalar(select(ContentVersion).order_by(ContentVersion.version.desc()))
    assert latest.text == full["text"] and latest.version == 2


async def test_completed_flag_creates_a_new_version_even_when_text_is_unchanged(session):
    short = parse_page(entry())[0]
    await ingest_items(session, "x_posts", [{**short, "truncated": True}], community="ai")
    result = await ingest_items(session, "x_posts", [short], community="ai")
    assert result.items_updated == 1


async def test_access_refusal_pauses_other_accounts_and_honors_retry_after(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "7200"})

    monkeypatch.setattr(x_module.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs))
    redis = LocalRedis()
    first = x_module.XCollector()
    first.redis = redis
    with pytest.raises(CollectorError):
        await first.fetch("account_dynamics", "openai", 20)
    assert await redis.pttl(x_module._COOLDOWN_KEY) > 7_190_000
    second = x_module.XCollector()
    second.redis = redis
    with pytest.raises(CollectorDeferred):
        await second.fetch("account_dynamics", "deepseek_ai", 20)
    assert requests == ["/openai"]


async def test_long_post_is_not_repeatedly_expanded_within_one_day(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    def handle(request):
        requests.append(request.url.path)
        if request.url.path == "/openai":
            return httpx.Response(200, text=entry(text="Codex new API Show more"))
        return httpx.Response(200, text='<meta property="og:url" content="https://x.com/OpenAI/status/100">'
            '<article><div dir="auto" class="whitespace-pre-wrap">Codex new API is available.</div></article>')

    monkeypatch.setattr(x_module.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(x_module, "_REQUEST_INTERVAL_RANGE_SECONDS", (0.001, 0.001))
    collector = x_module.XCollector()
    collector.redis = LocalRedis()
    first = await collector.fetch("account_dynamics", "openai", 20)
    assert first.items[0]["truncated"] is False
    await collector.fetch("account_dynamics", "openai", 20)
    assert requests == ["/openai", "/OpenAI/status/100", "/openai"]


@pytest.mark.parametrize("interval,failures,minimum,maximum", [
    (60, 0, 45, 75), (30, 0, 30, 37.5), (60, 2, 180, 300),
])
def test_x_schedule_varies_each_round_and_keeps_minimum_and_failure_backoff(interval, failures, minimum, maximum):
    target = CollectTarget(platform="x", interval_minutes=interval, consecutive_failures=failures)
    now = utcnow()
    delays = [(collect.next_run_with_jitter(target, now) - now).total_seconds() / 60 for _ in range(20)]
    assert all(minimum <= delay <= maximum for delay in delays)
    assert len(set(delays)) > 1


async def test_random_request_spacing_is_shared_by_profile_detail_and_other_accounts(monkeypatch):
    elapsed = 0.0
    requests, slots = [], []
    monkeypatch.setattr("app.services.queue.time", SimpleNamespace(monotonic=lambda: elapsed))

    async def advance(seconds):
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(x_module.asyncio, "sleep", advance)
    delays = iter([30, 60, 90])
    monkeypatch.setattr(x_module.random, "uniform", lambda *_: next(delays))

    class Redis(LocalRedis):
        async def set(self, key, value, nx=False, px=None):
            acquired = await super().set(key, value, nx=nx, px=px)
            if acquired:
                slots.append((key, px))
            return acquired

    def handle(request):
        assert "authorization" not in request.headers and "cookie" not in request.headers
        requests.append((request.url.path, elapsed))
        return httpx.Response(200, text="public page")

    first, second = x_module.XCollector(), x_module.XCollector()
    first.redis = second.redis = Redis()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await first._read(client, "https://x.com/openai")
        await first._read(client, "https://x.com/openai/status/100")
        await second._read(client, "https://x.com/deepseek_ai")
    assert [path for path, _ in requests] == ["/openai", "/openai/status/100", "/deepseek_ai"]
    assert requests[1][1] - requests[0][1] >= 30
    assert requests[2][1] - requests[1][1] >= 60
    assert slots == [("anisignal:politeness:x", 30_000), ("anisignal:politeness:x", 60_000), ("anisignal:politeness:x", 90_000)]


async def test_x_accounts_resume_at_different_times_after_shared_cooldown(session, session_factory, monkeypatch):
    monkeypatch.setattr(collect, "SessionLocal", session_factory)
    targets = [CollectTarget(platform="x", target_type="account_dynamics", target_value=account,
                             community="ai", interval_minutes=60, consecutive_failures=0)
               for account in ("openai", "deepseek_ai")]
    session.add_all(targets)
    await session.commit()
    redis = LocalRedis()
    await redis.set(x_module._COOLDOWN_KEY, "1", px=7_200_000)
    results = [await collect.run_collection(redis, target.id) for target in targets]
    for target in targets:
        await session.refresh(target)
        assert target.consecutive_failures == 0 and target.last_success_at is None
        assert utcnow() + timedelta(minutes=120) < target.next_run_at < utcnow() + timedelta(minutes=130)
    assert all(result["status"] == "skipped" for result in results)
    assert targets[0].next_run_at != targets[1].next_run_at
