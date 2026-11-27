"""通用周期报告（日报 / 周报）、订阅推送、Webhook 卡片与 include_in_digest 回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
import json

from sqlalchemy import event, select

from app.clock import utcnow
from app.models import (
    AiUsage,
    ContentBrief,
    ContentVersion,
    CustomFeed,
    CustomFeedItem,
    MetricSnapshot,
    NotificationDelivery,
    PeriodicReport,
    RawContent,
    ReportSubscription,
    Source,
    User,
    UserWebhook,
)
from app.services import periodic_reports as pr
from app.services.auth import SESSION_COOKIE, create_session, hash_password
from app.services.notifications import load_candidates
from app.services.webhooks import build_webhook_request


async def _create_verified_user(session, email: str) -> tuple[User, str]:
    user = User(
        email=email,
        password_hash=hash_password("Pass123456"),
        is_verified=True,
        notification_enabled=True,
    )
    session.add(user)
    await session.flush()
    token = await create_session(session, user)
    await session.commit()
    return user, token


async def _seed_community_items(session, community: str = "wuwa", *, hours_ago: int = 2):
    now = utcnow()
    src = Source(platform="github", external_id=f"gh-{community}", name="OpenRepo", identity="official")
    session.add(src)
    await session.flush()

    r1 = RawContent(
        platform="github",
        content_type="release",
        external_id=f"{community}:rel:v2.0",
        community=community,
        source_id=src.id,
        url="https://github.com/org/repo/releases/tag/v2.0",
        published_at=now - timedelta(hours=hours_ago),
        raw_json={"big": "x" * 2048},
    )
    r2 = RawContent(
        platform="github",
        content_type="repo",
        external_id=f"{community}:repo:demo",
        community=community,
        source_id=src.id,
        url="https://github.com/org/demo",
        published_at=now - timedelta(hours=hours_ago + 1),
        raw_json={"big": "y" * 2048},
    )
    session.add_all([r1, r2])
    await session.flush()
    session.add_all(
        [
            ContentVersion(
                raw_content_id=r1.id,
                version=1,
                title="v2.0 正式发布",
                text="新增周期报告与 Webhook 卡片支持。",
                category="official_announce",
            ),
            ContentVersion(
                raw_content_id=r2.id,
                version=1,
                title="org/demo",
                text="A fast local signal engine.",
                category="community",
            ),
        ]
    )
    session.add(
        ContentBrief(
            raw_content_id=r2.id,
            kind="repo",
            headline="轻量本地信号引擎",
            tagline="提供低内存多源聚合与结构化摘要",
            summary="轻量本地信号引擎支持多源采集与定时推送。",
            highlights=["低内存", "多源聚合"],
            use_cases=["自托管"],
            tags=["开源", "工具"],
            model="test",
        )
    )
    session.add(MetricSnapshot(raw_content_id=r2.id, metrics={"like": 3200, "view": 450}))
    await session.commit()
    return r1, r2


async def test_deterministic_community_report_generation_reuse_and_sort_guard(session):
    await _seed_community_items(session, "wuwa")

    captured_sql: list[str] = []
    engine = session.bind.sync_engine

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        captured_sql.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        report = await pr.generate_or_get_report(
            session, scope_type="community", scope_key="wuwa", period="weekly"
        )
        await session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert report is not None
    assert report.ai_generated is False
    assert report.item_count == 2
    assert len(report.sections) == 2
    labels = [sec["label"] for sec in report.sections]
    assert "版本变更" in labels
    assert "热门开源项目" in labels
    assert "轻量本地信号引擎" in report.markdown
    assert "https://github.com/org/repo/releases/tag/v2.0" in report.markdown

    # 幂等复用：同刊期第二次调用返回同一条记录
    reused = await pr.generate_or_get_report(
        session, scope_type="community", scope_key="wuwa", period="weekly"
    )
    assert reused is not None
    assert reused.id == report.id

    # 排序查询不得携带大列（防 MySQL 1038）
    for sql in captured_sql:
        upper = sql.upper()
        if "ORDER BY" not in upper or "RAW_CONTENTS" not in upper:
            continue
        select_head = sql.split("FROM", 1)[0].lower()
        for col in ("raw_contents.raw_json", "sources.raw_json", "content_versions.text", "content_versions.normalized_json"):
            assert col not in select_head, f"Wide column {col} in ORDER BY query: {sql}"


async def test_ai_report_clustering_grounds_urls_and_falls_back_on_error(session, monkeypatch):
    await _seed_community_items(session, "wuwa")

    async def _fake_cfg(_session, **_kw):
        return {
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test",
            "api_keys": ["sk-test"],
            "model": "gpt-4o-mini",
            "price_in": 0.1,
            "price_out": 0.2,
            "task_models": {},
            "daily_token_limit": 0,
            "daily_cost_limit": 0.0,
        }

    async def _fake_chat(_config, _sys, _user, **_kw):
        return (
            json.dumps(
                {
                    "overview": "本期开源项目版本更新活跃，重点涵盖新版本发布与引擎工具。",
                    "sections": [
                        {
                            "label": "重点进展",
                            "summary": "版本与核心项目同步推进",
                            "item_indices": [1, 99, 2],  # 99 越界应自动过滤
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {"prompt_tokens": 100, "completion_tokens": 60},
        )

    monkeypatch.setattr(pr, "get_ai_config", _fake_cfg)
    monkeypatch.setattr(pr, "chat", _fake_chat)

    report = await pr.generate_or_get_report(
        session, scope_type="community", scope_key="wuwa", period="daily", force=True
    )
    await session.commit()

    assert report is not None
    assert report.ai_generated is True
    assert report.model == "gpt-4o-mini"
    assert report.item_count == 2
    assert report.sections[0]["label"] == "重点进展"
    urls = {item["url"] for item in report.sections[0]["items"]}
    assert urls == {
        "https://github.com/org/repo/releases/tag/v2.0",
        "https://github.com/org/demo",
    }
    usage_row = await session.scalar(select(AiUsage).where(AiUsage.task == "periodic_report"))
    assert usage_row is not None
    assert usage_row.status == "ok"

    # AI 异常时无缝降级回确定性报告
    async def _broken_chat(_config, _sys, _user, **_kw):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(pr, "chat", _broken_chat)
    fallback = await pr.generate_or_get_report(
        session, scope_type="community", scope_key="wuwa", period="daily", force=True
    )
    await session.commit()
    assert fallback is not None
    assert fallback.ai_generated is False
    assert fallback.item_count == 2


def test_webhook_periodic_report_formatting_all_providers():
    payload = {
        "type": "periodic_report",
        "report_id": 42,
        "title": "开源周报 · 2026-W48",
        "summary": "本周开源社区发布多款新工具。",
        "markdown": "# 开源周报\n\n- demo",
        "sections": [
            {
                "label": "版本变更",
                "summary": "共 1 条更新",
                "items": [
                    {
                        "title": "v2.0 正式发布",
                        "url": "https://github.com/org/repo/releases/tag/v2.0",
                        "metrics_text": "★ 3.2k",
                        "source": "OpenRepo",
                    }
                ],
            }
        ],
        "period": "weekly",
        "period_key": "2026-W48",
        "scope_type": "community",
        "scope_key": "git",
        "community_label": "开源",
        "item_count": 1,
        "ai_generated": True,
        "url": "https://example.com/reports/42",
    }

    for provider in ("feishu", "dingtalk", "wecom", "discord", "bark", "ntfy", "generic"):
        target_url, headers, body = build_webhook_request(
            provider,
            "https://hooks.example.com/test",
            "my-secret",
            payload,
            now_ts=1700000000,
        )
        assert target_url.startswith("https://hooks.example.com/test")
        if provider == "ntfy":
            assert headers["Click"] == "https://example.com/reports/42"
            assert "本周开源社区发布多款新工具" in body.decode("utf-8")
        else:
            data = json.loads(body.decode("utf-8"))
            if provider == "feishu":
                assert data["msg_type"] == "interactive"
                assert data["card"]["header"]["template"] == "indigo"
                assert "sign" in data
                assert "阅读完整报告" in data["card"]["elements"][0]["content"]
            elif provider == "dingtalk":
                assert data["msgtype"] == "markdown"
                assert "sign=" in target_url
                assert "v2.0 正式发布" in data["markdown"]["text"]
            elif provider == "wecom":
                assert data["msgtype"] == "markdown"
                assert "阅读完整报告" in data["markdown"]["content"]
            elif provider == "discord":
                assert data["embeds"][0]["title"] == "开源周报 · 2026-W48"
                assert data["embeds"][0]["fields"][0]["name"] == "版本变更"
            elif provider == "bark":
                assert data["url"] == "https://example.com/reports/42"
                assert data["group"] == "AniSignal 报告"
            elif provider == "generic":
                assert data["type"] == "periodic_report"
                assert data["report"]["id"] == 42
                assert headers["X-AniSignal-Signature"].startswith("sha256=")


async def test_subscription_api_private_custom_report_and_scheduled_dispatch(
    client, session, admin_headers, monkeypatch
):
    pr._MANUAL_LAST_AT.clear()
    await _seed_community_items(session, "wuwa")
    user_a, token_a = await _create_verified_user(session, "alice@example.com")
    _user_b, token_b = await _create_verified_user(session, "bob@example.com")

    # 为 Alice 建定制监控与条目
    feed = CustomFeed(
        user_id=user_a.id,
        name="自建追踪",
        kind="gh_releases",
        platform="github",
        target_value="org/repo",
        enabled=True,
        status="active",
    )
    session.add(feed)
    await session.flush()
    session.add(
        CustomFeedItem(
            feed_id=feed.id,
            platform="github",
            content_type="release",
            external_id="rel-1",
            title="v3.1.0 发布说明",
            url="https://github.com/org/repo/releases/tag/v3.1.0",
            published_at=utcnow() - timedelta(hours=3),
        )
    )
    await session.commit()

    # Alice 创建仅接收周期报告的 Webhook
    wh_resp = await client.post(
        "/api/account/webhooks",
        cookies={SESSION_COOKIE: token_a},
        json={
            "name": "飞书周报群",
            "provider": "feishu",
            "url": "https://open.feishu.cn/open-apis/bot/v2/hook/demo",
            "events_mode": "reports",
        },
    )
    assert wh_resp.status_code == 201
    wh_id = wh_resp.json()["id"]

    # 创建两条订阅：1) 私有定制周报 → Webhook；2) 公开分区日报 → 邮件
    sub1 = await client.post(
        "/api/account/reports/subscriptions",
        cookies={SESSION_COOKIE: token_a},
        json={
            "scope_type": "custom_bundle",
            "scope_key": "all",
            "period": "weekly",
            "channel": "webhook",
            "webhook_id": wh_id,
        },
    )
    assert sub1.status_code == 201
    sub1_id = sub1.json()["id"]

    sub2 = await client.post(
        "/api/account/reports/subscriptions",
        cookies={SESSION_COOKIE: token_a},
        json={
            "scope_type": "community",
            "scope_key": "wuwa",
            "period": "daily",
            "channel": "email",
        },
    )
    assert sub2.status_code == 201
    sub2_id = sub2.json()["id"]

    sent_webhooks: list[dict] = []
    sent_emails: list[tuple[str, str]] = []

    async def _fake_send_wh(provider, url, secret, payload):
        sent_webhooks.append({"provider": provider, "payload": payload})
        return True, 200, None

    async def _fake_send_mail(to, subject, body, **_kw):
        sent_emails.append((to, subject))
        return True

    monkeypatch.setattr(pr, "send_webhook_http", _fake_send_wh)
    monkeypatch.setattr(pr, "send_email", _fake_send_mail)

    # 手动立即发送私有定制周报
    send_resp = await client.post(
        f"/api/account/reports/subscriptions/{sub1_id}/send",
        cookies={SESSION_COOKIE: token_a},
        json={"force": False},
    )
    assert send_resp.status_code == 200
    send_data = send_resp.json()
    assert send_data["ok"] is True
    private_report_id = send_data["report_id"]
    assert private_report_id is not None
    assert len(sent_webhooks) == 1
    assert sent_webhooks[0]["payload"]["type"] == "periodic_report"

    # 冷却拦截（60s 内再次手动发送返回 429）
    throttled = await client.post(
        f"/api/account/reports/subscriptions/{sub1_id}/send",
        cookies={SESSION_COOKIE: token_a},
        json={"force": False},
    )
    assert throttled.status_code == 429

    # 私有定制报告权限隔离：Alice 可看，匿名与 Bob 均 404，公开列表不泄露
    r_owner = await client.get(f"/api/reports/{private_report_id}", cookies={SESSION_COOKIE: token_a})
    assert r_owner.status_code == 200
    assert r_owner.json()["item_count"] == 1
    r_anon = await client.get(f"/api/reports/{private_report_id}")
    assert r_anon.status_code == 404
    r_bob = await client.get(f"/api/reports/{private_report_id}", cookies={SESSION_COOKIE: token_b})
    assert r_bob.status_code == 404
    r_pub = await client.get("/api/reports")
    assert r_pub.status_code == 200
    assert all(item["id"] != private_report_id for item in r_pub.json()["items"])
    r_mine = await client.get("/api/reports", params={"mine": "true"}, cookies={SESSION_COOKIE: token_a})
    assert r_mine.status_code == 200
    assert [item["id"] for item in r_mine.json()["items"]] == [private_report_id]

    # 定时批次测试：在周二执行只跑日报（不重复跑周报），且同周期二次调用幂等去重
    now_utc = utcnow()
    # 找到相对当前时间最近的过去周二 00:35 UTC（北京时间 08:35）
    days_since_tue = (now_utc.weekday() - 1) % 7
    if days_since_tue == 0:
        days_since_tue = 7
    tuesday_utc = (now_utc - timedelta(days=days_since_tue)).replace(hour=0, minute=35, second=0, microsecond=0)
    # 确保测试数据落在 tuesday_utc 的 24h 窗口内
    raws = list(await session.scalars(select(RawContent).where(RawContent.community == "wuwa")))
    for raw in raws:
        raw.published_at = tuesday_utc - timedelta(hours=2)
    await session.commit()

    stats1 = await pr.run_scheduled_reports(session, now=tuesday_utc)
    assert stats1["due"] == 1
    assert stats1["sent"] == 1
    assert len(sent_emails) == 1

    stats2 = await pr.run_scheduled_reports(session, now=tuesday_utc)
    assert stats2["sent"] == 0
    assert stats2["skipped"] == 1

    # 恢复发布时间到当前近 2 小时内，测试管理端按需生成公开分区报告
    for raw in raws:
        raw.published_at = utcnow() - timedelta(hours=2)
    await session.commit()

    admin_gen = await client.post(
        "/api/admin/reports/generate",
        headers=admin_headers,
        json={"community": "wuwa", "period": "weekly", "force": True},
    )
    assert admin_gen.status_code == 200
    assert admin_gen.json()["generated"] is True
    pub_id = admin_gen.json()["report"]["id"]

    # 公开报告可匿名阅读
    r_pub_detail = await client.get(f"/api/reports/{pub_id}")
    assert r_pub_detail.status_code == 200
    assert r_pub_detail.json()["scope_key"] == "wuwa"

    # 清理订阅
    del_resp = await client.delete(
        f"/api/account/reports/subscriptions/{sub2_id}",
        cookies={SESSION_COOKIE: token_a},
    )
    assert del_resp.status_code == 204
    pr._MANUAL_LAST_AT.clear()


async def test_feed_community_include_in_digest_controls_morning_candidates(client, session, admin_headers):
    # 新建一个独立主题流分区，默认 include_in_digest=False
    create_resp = await client.post(
        "/api/admin/communities",
        headers=admin_headers,
        json={
            "key": "devfeed",
            "label": "开发情报",
            "domain": "feed",
            "color": "blue",
            "active": True,
            "ai_summary": False,
            "include_in_digest": False,
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["include_in_digest"] is False

    await _seed_community_items(session, "devfeed", hours_ago=1)
    now = utcnow()
    cands_off = await load_candidates(session, None, now - timedelta(hours=24), now + timedelta(minutes=5))
    assert not any(c["community"] == "devfeed" for c in cands_off)

    # 开启 include_in_digest=True 后进入早报候选
    upd_resp = await client.put(
        "/api/admin/communities/devfeed",
        headers=admin_headers,
        json={"include_in_digest": True},
    )
    assert upd_resp.status_code == 200
    assert upd_resp.json()["include_in_digest"] is True

    cands_on = await load_candidates(session, None, now - timedelta(hours=24), now + timedelta(minutes=5))
    assert any(c["community"] == "devfeed" for c in cands_on)
