"""Admin API 单测：鉴权、CRUD、重复冲突、手动触发（队列注入桩）。"""

import pytest

from app.api import main as api_main
from app.models import CollectTarget


class _FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[int] = []
        self.notifications: list[int] = []
        self.reviewed: list[int] = []
        self.admin_emails: list[int] = []

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

    async def enqueue_review(self, content_ids: list[int]) -> str | None:
        self.reviewed.extend(content_ids)
        return "fake-review-job"

    async def enqueue_admin_email(self, delivery_ids: list[int]) -> str | None:
        self.admin_emails.extend(delivery_ids)
        return "fake-admin-email-job"


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


async def test_target_value_rejects_option_like_and_malformed_values(client, admin_headers):
    """target_value 会进 bili CLI argv：入口必须挡住选项注入与格式错误（SEC-025/032）。

    注意真正的注入向量是「以 - 开头」——argv 是列表，`鸣潮 --type user` 整串只是
    一个位置参数，不会被 argparse 当成选项；只有以 `-` 开头的值才会被解析成选项。
    """
    resp = await client.post(
        "/api/admin/collect-targets",
        json={
            "platform": "bilibili",
            "target_type": "keyword_search",
            "target_value": "-o /tmp/x",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/api/admin/collect-targets",
        json={
            "platform": "bilibili",
            "target_type": "keyword_search",
            "target_value": "--type user",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422

    # 账号型目标必须是数字 MID
    resp = await client.post(
        "/api/admin/collect-targets",
        json={"platform": "bilibili", "target_type": "account_videos", "target_value": "1955897084 -o x"},
        headers=admin_headers,
    )
    assert resp.status_code == 422

    # 控制字符同样拒绝
    resp = await client.post(
        "/api/admin/collect-targets",
        json={
            "platform": "bilibili",
            "target_type": "keyword_search",
            "target_value": "鸣潮\nBcc: x@y.z",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422

    # 合法值照常通过
    resp = await client.post(
        "/api/admin/collect-targets",
        json={
            "platform": "bilibili",
            "target_type": "account_dynamics",
            "target_value": "1955897084",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201
    target_id = resp.json()["id"]

    # 更新路径同样校验（按既有 platform/target_type 判）
    resp = await client.put(
        f"/api/admin/collect-targets/{target_id}",
        json={"target_value": "--no-cache"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


async def test_bili_collector_refuses_unauthorized_options():
    """argv 白名单：外部数据里的选项不许进 create_subprocess_exec（SEC-025）。"""
    import pytest

    from app.collectors.bilibili import BilibiliCollector
    from app.collectors.base import CollectorError

    collector = BilibiliCollector()
    for bad in (
        ("search", "鸣潮", "-o", "/tmp/x"),
        ("dynamics", "123", "--output", "json"),
        ("user", "-n", "10"),
        ("video", "-o", "json"),
    ):
        with pytest.raises(CollectorError):
            await collector._run(*bad)


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


async def test_content_moderation_flow(client, admin_headers, session):
    """管理端内容治理：下架/恢复、公开侧过滤、修订产生新版本、删除、重审入队。"""
    from sqlalchemy import select

    from app.models import RawContent, ContentVersion
    from app.services.ingest import ingest_items
    from tests.test_parsers_bilibili import VIDEO_RAW

    await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW, bvid="BVmod", title="鸣潮联动活动情报")])
    raw = await session.scalar(select(RawContent).where(RawContent.external_id == "BVmod"))
    assert raw is not None

    # 管理列表带 hidden 与审核字段
    resp = await client.get("/api/admin/contents", headers=admin_headers)
    item = next(i for i in resp.json()["items"] if i["id"] == raw.id)
    assert item["hidden"] is False
    assert item["review"] is None

    # 公开侧可见
    resp = await client.get("/api/contents")
    assert any(i["id"] == raw.id for i in resp.json()["items"])

    # 下架 → 公开列表与详情都排除
    resp = await client.patch(
        f"/api/admin/contents/{raw.id}/visibility", json={"hidden": True}, headers=admin_headers
    )
    assert resp.status_code == 200 and resp.json()["hidden"] is True
    resp = await client.get("/api/contents")
    assert not any(i["id"] == raw.id for i in resp.json()["items"])
    resp = await client.get(f"/api/contents/{raw.id}")
    assert resp.status_code == 404

    # 管理端筛选 hidden=true
    resp = await client.get("/api/admin/contents", params={"hidden": "true"}, headers=admin_headers)
    assert any(i["id"] == raw.id for i in resp.json()["items"])

    # 恢复
    await client.patch(
        f"/api/admin/contents/{raw.id}/visibility", json={"hidden": False}, headers=admin_headers
    )
    resp = await client.get(f"/api/contents/{raw.id}")
    assert resp.status_code == 200

    # 修订标题 → 新版本，公开详情展示新标题
    resp = await client.patch(
        f"/api/admin/contents/{raw.id}",
        json={"title": "鸣潮联动活动情报（已修订）", "text": None},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    new_version = resp.json()["version"]
    assert new_version >= 2
    versions = await session.scalars(
        select(ContentVersion).where(ContentVersion.raw_content_id == raw.id)
    )
    assert len(list(versions)) == new_version  # 旧版本保留
    resp = await client.get(f"/api/contents/{raw.id}")
    assert resp.json()["title"] == "鸣潮联动活动情报（已修订）"

    # 重审：清掉旧判定并入队
    resp = await client.post(f"/api/admin/contents/{raw.id}/review", headers=admin_headers)
    assert resp.status_code == 200
    from tests.test_admin_api import _FakeQueue

    fake = api_main.app.state.queue
    assert raw.id in fake.reviewed

    # 删除 → 管理端与公开侧都消失
    resp = await client.delete(f"/api/admin/contents/{raw.id}", headers=admin_headers)
    assert resp.status_code == 204
    remaining = await session.scalar(
        select(RawContent.id).where(RawContent.id == raw.id)
    )
    assert remaining is None
    resp = await client.get("/api/admin/contents", headers=admin_headers)
    assert not any(i["id"] == raw.id for i in resp.json()["items"])


async def test_target_update_applies_thresholds(client, admin_headers):
    """更新端点应生效 min_views/co_terms（分级策略依赖的门槛配置）。"""
    resp = await client.post(
        "/api/admin/collect-targets",
        json={
            "platform": "bilibili",
            "target_type": "keyword_search",
            "target_value": "门槛测试词",
            "min_views": 0,
            "tier": "general",
        },
        headers=admin_headers,
    )
    target_id = resp.json()["id"]
    resp = await client.put(
        f"/api/admin/collect-targets/{target_id}",
        json={"min_views": 10000, "co_terms": ["版本", "共鸣"]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["min_views"] == 10000
    assert body["co_terms"] == ["版本", "共鸣"]


async def test_contents_tier_and_runs_fields(client, admin_headers, session):
    """内容列表带来源分级与筛选；运行端点带过滤细分字段。"""
    from sqlalchemy import select

    from app.models import CollectRun, CollectTarget, RawContent
    from app.services.ingest import ingest_items
    from tests.test_parsers_bilibili import VIDEO_RAW

    session.add(
        CollectTarget(
            platform="bilibili",
            target_type="keyword_search",
            target_value="爱弥斯",
            tier="core",
        )
    )
    await session.commit()
    await ingest_items(
        session,
        "bilibili_search",
        [dict(VIDEO_RAW, bvid="BVtier1", title="爱弥斯键盘实拍", matched_keywords=None)],
    )
    raw = await session.scalar(select(RawContent).where(RawContent.external_id == "BVtier1"))
    raw.matched_keywords = ["爱弥斯"]
    await session.commit()

    resp = await client.get("/api/admin/contents", headers=admin_headers)
    item = next(i for i in resp.json()["items"] if i["id"] == raw.id)
    assert item["tier"] == "core"
    assert "爱弥斯" in item["matched_keywords"]

    resp = await client.get("/api/admin/contents", params={"tier": "core"}, headers=admin_headers)
    assert any(i["id"] == raw.id for i in resp.json()["items"])
    resp = await client.get("/api/admin/contents", params={"tier": "general"}, headers=admin_headers)
    assert not any(i["id"] == raw.id for i in resp.json()["items"])

    # runs 端点带过滤细分字段
    session.add(
        CollectRun(
            target_id=(await session.scalar(select(CollectTarget.id).limit(1))),
            started_at=__import__("app.clock", fromlist=["utcnow"]).utcnow(),
            status="success",
            items_found=20,
            items_new=5,
            items_fetched=25,
            items_filtered=5,
        )
    )
    await session.commit()
    resp = await client.get("/api/admin/runs?limit=5", headers=admin_headers)
    run = resp.json()[0]
    assert "items_fetched" in run and "items_filtered" in run


async def test_contents_review_filter_accuracy(client, admin_headers, session):
    """审核结果筛选必须精确：violation 只返回违规内容，不产生笛卡尔积膨胀。"""
    from sqlalchemy import select

    from app.models import ContentReview, RawContent
    from app.services.ingest import ingest_items
    from tests.test_parsers_bilibili import VIDEO_RAW

    await ingest_items(
        session,
        "bilibili_search",
        [
            dict(VIDEO_RAW, bvid="BVfilt-v", title="违规测试内容"),
            dict(VIDEO_RAW, bvid="BVfilt-ok", title="正常测试内容"),
            dict(VIDEO_RAW, bvid="BVfilt-nr", title="无审核内容"),
        ],
    )
    rows = {r.external_id: r.id for r in await session.scalars(select(RawContent))}
    session.add(
        ContentReview(
            raw_content_id=rows["BVfilt-v"], relevant=True, promotional=False,
            violation=True, quality_score=10, model="test",
        )
    )
    session.add(
        ContentReview(
            raw_content_id=rows["BVfilt-ok"], relevant=True, promotional=False,
            violation=False, quality_score=80, model="test",
        )
    )
    # 无审核内容不给 review 行
    await session.commit()

    resp = await client.get(
        "/api/admin/contents", params={"review": "violation", "limit": 200}, headers=admin_headers
    )
    items = resp.json()["items"]
    assert resp.json()["total"] == 1
    assert items[0]["external_id"] == "BVfilt-v"
    assert items[0]["review"]["violation"] is True

    resp = await client.get(
        "/api/admin/contents", params={"review": "ok", "limit": 200}, headers=admin_headers
    )
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["external_id"] == "BVfilt-ok"

    # 无审核行的内容不被 ok/violation 命中
    resp = await client.get(
        "/api/admin/contents", params={"review": "irrelevant", "limit": 200}, headers=admin_headers
    )
    assert resp.json()["total"] == 0
