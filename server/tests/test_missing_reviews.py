"""采集后的审核只投递一次；聚合死锁、发布重启、批量迁移时漏掉的游戏内容由定时任务补上。"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.clock import utcnow
from app.models import AiUsage, ContentReview, RawContent, Source
from app.services.curation import MISSING_REVIEW_GRACE, MISSING_REVIEW_WINDOW, missing_review_ids


async def _content(session, external, *, community="wuwa", age=timedelta(hours=1), hidden=False):
    source = await session.get(Source, 1)
    if source is None:
        source = Source(id=1, platform="bilibili", external_id="author", name="作者")
        session.add(source)
        await session.flush()
    raw = RawContent(platform="bilibili", content_type="video", external_id=external, community=community,
                     source_id=source.id, url=f"https://example.com/{external}", raw_json={},
                     collected_at=utcnow() - age, hidden=hidden, content_hash=external)
    session.add(raw)
    await session.flush()
    return raw


async def test_only_recent_visible_unreviewed_game_content_is_picked(session):
    missed = await _content(session, "missed")
    nikke = await _content(session, "nikke", community="nikke", age=timedelta(days=1))
    reviewed = await _content(session, "reviewed")
    session.add(ContentReview(raw_content_id=reviewed.id, relevant=True, model="m"))
    await _content(session, "fresh", age=MISSING_REVIEW_GRACE - timedelta(minutes=5))  # 正常链路还在处理
    await _content(session, "old", age=MISSING_REVIEW_WINDOW + timedelta(hours=1))
    await _content(session, "hidden", hidden=True)
    await _content(session, "news", community="ai")  # 快讯走 pending_news_ids
    await session.commit()

    assert await missing_review_ids(session) == [missed.id, nikke.id]


async def test_repeated_model_failures_stop_the_backfill(session):
    raw = await _content(session, "flaky")
    for minutes in (40, 30, 20):
        session.add(AiUsage(task="review_content", ref_id=raw.id, model="m", status="failed",
                            created_at=utcnow() - timedelta(minutes=minutes)))
    await session.commit()
    assert await missing_review_ids(session) == []


async def test_sweep_enqueues_each_missing_review_once_per_cooldown(session, session_factory, monkeypatch):
    from app.worker.settings import MISSING_REVIEW_COOLDOWN_MS, review_pending_news

    raw = await _content(session, "missed")
    await session.commit()
    claimed: set[str] = set()

    async def set_once(key, value, nx=False, px=None):
        assert nx and px == MISSING_REVIEW_COOLDOWN_MS
        if key in claimed:
            return None
        claimed.add(key)
        return True

    redis = SimpleNamespace(set=set_once, enqueue_job=AsyncMock(return_value=SimpleNamespace(job_id="j")))
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    assert (await review_pending_news({"redis": redis}))["missing_reviews"] == 1
    assert redis.enqueue_job.await_args.args == ("review_contents", [raw.id])
    # 审核端按规则跳过时不会留失败记录，冷却锁保证不会每 5 分钟重复投递。
    assert (await review_pending_news({"redis": redis}))["missing_reviews"] == 0
    assert redis.enqueue_job.await_count == 1
