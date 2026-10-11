from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.queue import enqueue_review_jobs
from app.worker.settings import WorkerSettings


async def test_review_jobs_deduplicate_content_ids_and_use_stable_keys():
    redis = SimpleNamespace(enqueue_job=AsyncMock(side_effect=[None, SimpleNamespace(job_id="review-content:2")]))
    assert await enqueue_review_jobs(redis, [1, 1, 2]) == "review-content:2"
    assert [call.args for call in redis.enqueue_job.await_args_list] == [
        ("review_contents", [1]), ("review_contents", [2]),
    ]
    assert [call.kwargs for call in redis.enqueue_job.await_args_list] == [
        {"_job_id": "review-content:1"}, {"_job_id": "review-content:2"},
    ]
    review = next(item for item in WorkerSettings.functions if getattr(item, "name", None) == "review_contents")
    assert review.keep_result_s == 0
    assert review.max_tries == 3


async def test_failed_review_returns_worker_slot_and_defers_retry(session_factory, monkeypatch):
    import pytest
    from arq.worker import Retry
    from app.services.curation import ReviewStats
    from app.worker.settings import review_contents

    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    monkeypatch.setattr("app.services.curation.review_contents", AsyncMock(return_value=ReviewStats(failed_batches=1)))
    with pytest.raises(Retry) as result:
        await review_contents({}, [123])
    assert result.value.defer_score == 600_000


async def test_extraction_sweep_lock_skips_overlap_and_releases_on_failure(session_factory, monkeypatch):
    import pytest
    from app.worker.settings import extract_entities_sweep

    redis = SimpleNamespace(set=AsyncMock(return_value=False), eval=AsyncMock())
    sweep = AsyncMock(side_effect=RuntimeError("temporary"))
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    monkeypatch.setattr("app.services.extract.sweep_extractions", sweep)
    assert await extract_entities_sweep({"redis": redis}) == {"status": "busy"}
    sweep.assert_not_awaited()
    redis.set.return_value = True
    with pytest.raises(RuntimeError):
        await extract_entities_sweep({"redis": redis})
    assert redis.eval.await_args.args[2] == "anisignal:extract-sweep-lock"
    assert redis.eval.await_args.args[3] == redis.set.await_args.args[1]
