"""Admin API 单测：鉴权、CRUD、重复冲突、手动触发（队列注入桩）。"""

import pytest

from app.api import main as api_main
from app.models import CollectTarget


class _FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[int] = []
        self.notifications: list[int] = []

    async def enqueue_collect(self, target_id: int) -> str | None:
        self.enqueued.append(target_id)
        return "fake-job-id"

    async def pending_count(self) -> int | None:
        return 3

    async def enqueue_notification(self, delivery_id: int) -> str | None:
        self.notifications.append(delivery_id)
        return "fake-notification-job"

    async def enqueue_morning(self) -> str | None:
        return "fake-morning-job"


async def test_healthz_open(client):
    resp = await client.get("/api/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_admin_requires_token(client):
    resp = await client.get("/api/admin/collect-targets")
    assert resp.status_code == 422  # 缺 Header
    resp = await client.get("/api/admin/collect-targets", headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401


async def test_target_crud_flow(client, admin_headers, session):
    payload = {
        "platform": "bilibili",
        "target_type": "keyword_search",
        "target_value": "鸣潮",
        "interval_minutes": 30,
    }
    resp = await client.post("/api/admin/collect-targets", json=payload, headers=admin_headers)
    assert resp.status_code == 201
    created = resp.json()
    assert created["platform"] == "bilibili"
    assert created["next_run_at"] is not None

    # 重复创建 → 409
    resp = await client.post("/api/admin/collect-targets", json=payload, headers=admin_headers)
    assert resp.status_code == 409

    # 列表
    resp = await client.get("/api/admin/collect-targets", headers=admin_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 更新频率与启停
    resp = await client.put(
        f"/api/admin/collect-targets/{created['id']}",
        json={"interval_minutes": 60, "enabled": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["interval_minutes"] == 60
    assert resp.json()["enabled"] is False

    # 删除
    resp = await client.delete(
        f"/api/admin/collect-targets/{created['id']}", headers=admin_headers
    )
    assert resp.status_code == 204
    assert await session.get(CollectTarget, created["id"]) is None


async def test_trigger_run(client, admin_headers, fake_queue):
    resp = await client.post(
        "/api/admin/collect-targets",
        json={"platform": "bilibili", "target_type": "keyword_search", "target_value": "鸣潮"},
        headers=admin_headers,
    )
    target_id = resp.json()["id"]
    resp = await client.post(
        f"/api/admin/collect-targets/{target_id}/run", headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_enqueued"] is True
    assert body["job_id"] == "fake-job-id"
    assert fake_queue.enqueued == [target_id]


async def test_overview_and_contents_empty(client, admin_headers, fake_queue):
    resp = await client.get("/api/admin/overview", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["queue"]["pending_jobs"] == 3
    assert body["total_contents"] == 0

    resp = await client.get("/api/admin/contents", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


async def test_contents_listing_after_ingest(client, admin_headers, session):
    from app.services.ingest import ingest_items
    from tests.test_parsers_bilibili import VIDEO_RAW

    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    resp = await client.get("/api/admin/contents", headers=admin_headers)
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["external_id"] == "BV1Mcgp6ME1x"
    assert item["title"] == "视频标题"
    assert item["source_name"] == "UP主名称"
    assert item["latest_metrics"]["view"] == 680
