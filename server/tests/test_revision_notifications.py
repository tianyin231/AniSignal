"""官方公告实质修订（抓暗改）的全通道推送、RSS 与 Bot/插件集成测试。"""

import json
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import httpx
import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import (
    BotDelivery,
    BotTarget,
    ContentVersion,
    EmailCode,
    NotificationDelivery,
    RawContent,
    Source,
    User,
    UserWebhook,
)
from app.services import bot as bot_service
from app.services import instant_notifications as instant
from app.services import webhooks as webhooks_service
from app.services.bot_policy import AUTOMATIC_KINDS


async def _register_and_login(client, session, email="rev@t.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "password123"})
    code_row = await session.scalar(select(EmailCode).order_by(EmailCode.id.desc()).limit(1))
    resp = await client.post("/api/auth/verify", json={"email": email, "code": code_row.code_plain})
    assert resp.status_code == 200
    return await session.scalar(select(User).where(User.email == email))


async def _seed_official_notice(
    session,
    *,
    external_id: str,
    community: str = "wuwa",
    platform: str = "kuro_cn",
    identity: str = "official",
    title_v1: str = "2.8 版本维护更新公告",
    text_v1: str = "维护时间：06:00 - 11:00\n补偿：星声×300",
    media_v1: list[str] | None = None,
    published_at: datetime | None = None,
    parsed_at_v1: datetime | None = None,
) -> tuple[Source, RawContent, ContentVersion]:
    now = utcnow()
    src_ext = f"src-{community}-{platform}-{identity}"
    source = await session.scalar(
        select(Source).where(
            Source.platform == platform,
            Source.external_id == src_ext,
        )
    )
    if source is None:
        source = Source(
            platform=platform,
            name=f"{community}-{identity}-src",
            identity=identity,
            external_id=src_ext,
        )
        session.add(source)
        await session.flush()
    raw = RawContent(
        source_id=source.id,
        platform=platform,
        content_type="notice",
        external_id=external_id,
        url=f"https://example.com/{community}/{external_id}",
        community=community,
        published_at=published_at or (now - timedelta(hours=2)),
        content_hash=f"hash-{external_id}",
        raw_json={},
    )
    session.add(raw)
    await session.flush()
    v1 = ContentVersion(
        raw_content_id=raw.id,
        version=1,
        parsed_at=parsed_at_v1 or (now - timedelta(hours=2)),
        title=title_v1,
        text=text_v1,
        normalized_json={"media": media_v1 or [], "extra": {}},
        parser_version="2",
    )
    session.add(v1)
    await session.commit()
    return source, raw, v1


async def _add_version(
    session,
    raw: RawContent,
    version: int,
    *,
    title: str,
    text: str,
    media: list[str] | None = None,
    parsed_at: datetime | None = None,
) -> ContentVersion:
    ver = ContentVersion(
        raw_content_id=raw.id,
        version=version,
        parsed_at=parsed_at or utcnow(),
        title=title,
        text=text,
        normalized_json={"media": media or [], "extra": {}},
        parser_version="2",
    )
    session.add(ver)
    await session.commit()
    return ver


@pytest.mark.asyncio
async def test_email_revision_opt_in_no_replay_and_noise_filter(client, session, monkeypatch):
    user = await _register_and_login(client, session, email="emailrev@t.com")

    # 开启前的历史修订：开启后绝不补发
    _, old_raw, _ = await _seed_official_notice(session, external_id="old_notice")
    old_v2 = await _add_version(
        session,
        old_raw,
        2,
        title="2.8 版本维护更新公告",
        text="维护时间：06:00 - 12:00\n补偿：星声×300",
    )

    # 无需任何关键词即可开启官方修订邮件提醒
    res = await client.put("/api/account/notifications", json={"enabled": True, "revision_enabled": True})
    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is True
    assert data["revision_enabled"] is True

    await session.refresh(user)
    assert user.instant_revision_cursor == old_v2.id
    assert await instant.prepare_instant_notifications(session) == 0

    # 1) 风控丢图（正文未变，图片 2 -> 0）：不应触发推送
    _, noise_raw, _ = await _seed_official_notice(
        session,
        external_id="noise_notice",
        media_v1=["https://i0.hdslb.com/bfs/new_dyn/a.jpg", "https://i0.hdslb.com/bfs/new_dyn/b.jpg"],
    )
    await _add_version(
        session,
        noise_raw,
        2,
        title="2.8 版本维护更新公告",
        text="维护时间：06:00 - 11:00\n补偿：星声×300",
        media=[],
    )
    # 2) 非官方（community）修订：不应触发推送
    _, comm_raw, _ = await _seed_official_notice(
        session,
        external_id="comm_post",
        identity="community",
        title_v1="玩家攻略",
        text_v1="旧版配队",
    )
    await _add_version(session, comm_raw, 2, title="玩家攻略", text="新版配队")
    assert await instant.prepare_instant_notifications(session) == 0

    # 3) 真正的官方公告暗改（修改补偿与维护结束时间）：触发发信且只发一次
    _, real_raw, _ = await _seed_official_notice(
        session,
        external_id="real_notice",
        title_v1="卡提希娅活动说明",
        text_v1="活动截止：11月28日\n补偿：星声×300",
    )
    await _add_version(
        session,
        real_raw,
        2,
        title="卡提希娅活动说明",
        text="活动截止：11月30日\n补偿：星声×600",
    )
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(instant, "send_email", sender)
    result = await instant.run_instant_notifications(session)
    assert result == {"created": 1, "sent": 1, "failed": 0, "skipped": 0}
    sender.assert_awaited_once()

    delivery = await session.scalar(select(NotificationDelivery))
    assert delivery.dedupe_key.startswith(f"instant-revision:{user.id}:{real_raw.id}:")
    assert "官方修订" in delivery.subject
    assert "#revisions" in delivery.body
    assert "星声×600" in delivery.body

    # 再次运行不重复发送
    assert (await instant.run_instant_notifications(session))["sent"] == 0

    # 4) 屏蔽词生效：命中 exclude_keywords 时跳过
    user.feed_preferences = {"communities": ["wuwa"], "exclude_keywords": ["卡池"]}
    await session.commit()
    _, excluded_raw, _ = await _seed_official_notice(
        session,
        external_id="excl_notice",
        title_v1="卡池概率说明",
        text_v1="基础概率 0.8%",
    )
    await _add_version(session, excluded_raw, 2, title="卡池概率说明", text="基础概率 1.0%")
    assert await instant.prepare_instant_notifications(session) == 0

    # 5) 关闭 revision_enabled 会取消未发出的 instant-revision 投递
    _, pending_raw, _ = await _seed_official_notice(
        session,
        external_id="pending_notice",
        title_v1="版本活动预告",
        text_v1="开放时间 10:00",
    )
    await _add_version(session, pending_raw, 2, title="版本活动预告", text="开放时间 12:00")
    assert await instant.prepare_instant_notifications(session) == 1
    off_res = await client.put("/api/account/notifications", json={"revision_enabled": False})
    assert off_res.status_code == 200 and off_res.json()["revision_enabled"] is False
    cancelled = await session.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.dedupe_key.like(f"instant-revision:{user.id}:{pending_raw.id}:%")
        )
    )
    assert cancelled.status == "skipped"


@pytest.mark.asyncio
async def test_webhook_revisions_mode_and_include_revisions(client, session, monkeypatch):
    user = await _register_and_login(client, session, email="whrev@t.com")

    # 放行测试外网地址 DNS
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )

    # 先有一条历史修订，创建 webhook 时不应补发
    _, old_raw, _ = await _seed_official_notice(session, external_id="wh_old")
    await _add_version(session, old_raw, 2, title="2.8 版本维护更新公告", text="维护时间：06:00 - 12:00")

    # 创建仅修订通道 + 关键词附加修订通道
    r1 = await client.post(
        "/api/account/webhooks",
        json={
            "name": "仅暗改通道",
            "provider": "generic",
            "url": "https://hooks.example.com/rev-only",
            "events_mode": "revisions",
        },
    )
    assert r1.status_code == 201
    assert r1.json()["events_mode"] == "revisions"
    assert r1.json()["include_revisions"] is True

    r2 = await client.post(
        "/api/account/webhooks",
        json={
            "name": "飞书混合通道",
            "provider": "feishu",
            "url": "https://hooks.example.com/feishu-mix",
            "events_mode": "keywords",
            "include_revisions": False,
        },
    )
    assert r2.status_code == 201
    assert r2.json()["include_revisions"] is False

    # PATCH 开启 include_revisions
    p2 = await client.patch(f"/api/account/webhooks/{r2.json()['id']}", json={"include_revisions": True})
    assert p2.status_code == 200
    assert p2.json()["include_revisions"] is True

    sent_requests: list[tuple[str, dict]] = []

    def handler(request: httpx.Request):
        sent_requests.append((str(request.url), json.loads(request.content.decode("utf-8"))))
        return httpx.Response(200, json={"code": 0})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        webhooks_service.httpx,
        "AsyncClient",
        lambda *a, **kw: real_async_client(transport=transport),
    )

    # 历史修订不触发
    stats0 = await webhooks_service.run_webhook_notifications(session)
    assert stats0["sent"] == 0
    assert sent_requests == []

    # 新增一条实质官方修订：两个通道均收到
    _, new_raw, _ = await _seed_official_notice(
        session,
        external_id="wh_new",
        title_v1="2.9 维护补偿公告",
        text_v1="补偿：星声×300",
    )
    await _add_version(session, new_raw, 2, title="2.9 维护补偿公告", text="补偿：星声×600")

    stats1 = await webhooks_service.run_webhook_notifications(session)
    assert stats1["sent"] == 2
    assert len(sent_requests) == 2

    generic_body = next(body for url, body in sent_requests if url.endswith("/rev-only"))
    assert generic_body["type"] == "content_revision"
    assert generic_body["revision"]["content_id"] == new_raw.id
    assert generic_body["revision"]["from_version"] == 1
    assert generic_body["revision"]["to_version"] == 2

    feishu_body = next(body for url, body in sent_requests if url.endswith("/feishu-mix"))
    assert feishu_body["card"]["header"]["template"] == "orange"
    assert "官方公告修订" in feishu_body["card"]["header"]["title"]["content"]

    # 游标推进后不重发
    stats2 = await webhooks_service.run_webhook_notifications(session)
    assert stats2["sent"] == 0


@pytest.mark.asyncio
async def test_public_and_personal_rss_revision_feeds(client, session):
    user = await _register_and_login(client, session, email="rssrev@t.com")
    user.feed_preferences = {"communities": ["wuwa"], "exclude_keywords": ["排除词"]}
    await session.commit()

    # 1) 鸣潮官方实质修订
    _, wuwa_rev, _ = await _seed_official_notice(
        session,
        external_id="rss_wuwa_rev",
        community="wuwa",
        title_v1="鸣潮活动延期公告",
        text_v1="结束时间：11月25日",
    )
    await _add_version(session, wuwa_rev, 2, title="鸣潮活动延期公告", text="结束时间：11月27日")

    # 2) 原神官方实质修订（公开 RSS 包含，个人 RSS 因分区偏好仅 wuwa 被过滤）
    _, gs_rev, _ = await _seed_official_notice(
        session,
        external_id="rss_gs_rev",
        community="genshin",
        platform="miyoushe",
        title_v1="原神维护公告",
        text_v1="补偿原石 300",
    )
    await _add_version(session, gs_rev, 2, title="原神维护公告", text="补偿原石 600")

    # 3) 鸣潮未修订官方公告（revised_only=true 不应包含）
    await _seed_official_notice(
        session,
        external_id="rss_wuwa_plain",
        community="wuwa",
        title_v1="普通单版本公告",
        text_v1="没有改过",
    )

    # 公开 RSS ?revised_only=true
    pub_resp = await client.get("/api/rss.xml?community=all&revised_only=true")
    assert pub_resp.status_code == 200
    pub_root = ET.fromstring(pub_resp.text)
    pub_titles = [el.text for el in pub_root.findall("./channel/item/title")]
    pub_links = [el.text for el in pub_root.findall("./channel/item/link")]
    assert any("鸣潮活动延期公告" in t for t in pub_titles)
    assert any("原神维护公告" in t for t in pub_titles)
    assert not any("普通单版本公告" in t for t in pub_titles)
    assert all("#revisions" in link for link in pub_links)

    # 个人 RSS 令牌与 mode=revisions
    tok_resp = await client.get("/api/account/feed-token")
    assert tok_resp.status_code == 200
    revisions_path = tok_resp.json()["revisions_path"]
    assert "mode=revisions" in revisions_path

    priv_resp = await client.get(revisions_path)
    assert priv_resp.status_code == 200
    priv_root = ET.fromstring(priv_resp.text)
    priv_titles = [el.text for el in priv_root.findall("./channel/item/title")]
    assert len(priv_titles) == 1
    assert "鸣潮活动延期公告" in priv_titles[0]


@pytest.mark.asyncio
async def test_bot_revision_push_query_and_astrbot_config_roundtrip(session):
    assert "revisions" in AUTOMATIC_KINDS
    assert "revisions" in bot_service.TOOLS

    # 选一个明确的非静默时刻（北京时间 14:00 = UTC 06:00）
    now = datetime(2026, 11, 24, 6, 0, 0)

    # 一条 5 天前发布的官方旧公告在现在被暗改！
    _, old_notice, _ = await _seed_official_notice(
        session,
        external_id="bot_old_edited",
        community="wuwa",
        platform="kuro_cn",
        title_v1="版本前瞻活动规则",
        text_v1="参与即可获得星声×300",
        published_at=now - timedelta(days=5),
        parsed_at_v1=now - timedelta(days=5),
    )

    target = BotTarget(
        name="测试群",
        umo="napcat:GroupMessage:123456",
        enabled=True,
        last_scan_at=now - timedelta(minutes=10),
        config={
            "community": "wuwa",
            "communities": ["wuwa"],
            "scope_mode": "legacy",
            "daily_enabled": False,
            "updates_enabled": False,
            "revisions_enabled": True,
            "quiet_start": 23,
            "quiet_end": 8,
            "delivery_mode": "astrbot",
            "tools": ["digest", "news", "revisions"],
        },
    )
    session.add(target)
    await session.commit()

    # 发生实质修订（解析时间在 last_scan_at 之后）
    await _add_version(
        session,
        old_notice,
        2,
        title="版本前瞻活动规则",
        text="参与即可获得星声×600",
        parsed_at=now - timedelta(minutes=2),
    )

    await bot_service.run_bot_notifications(session, now=now)
    deliveries = list(
        await session.scalars(select(BotDelivery).where(BotDelivery.target_id == target.id, BotDelivery.kind == "revisions"))
    )
    assert len(deliveries) == 1
    row = deliveries[0]
    assert "官方公告修订" in row.body
    assert "星声×600" in row.body
    assert "#revisions" in row.body

    # prepare_delivery 必须接受（尽管原文 published_at 是 5 天前）
    assert await bot_service.prepare_delivery(session, row, target=target, now=now) is True

    # 关闭 revisions_enabled 后 prepare_delivery 取消积压
    target.config = {**target.config, "revisions_enabled": False}
    await session.commit()
    assert await bot_service.prepare_delivery(session, row, target=target, now=now) is False
    assert row.status == "cancelled"

    # Bot 查询工具 revisions（含关键词筛选）
    q_all = await bot_service.query_target(session, target, tool="revisions", now=now)
    assert len(q_all["items"]) == 1
    assert "版本前瞻活动规则" in q_all["text"]
    assert q_all["brief"]["mode"] == "revisions"

    q_miss = await bot_service.query_target(session, target, tool="revisions", query="不存在的词", now=now)
    assert q_miss["items"] == []

    # AstrBot 插件 configuration.py 往返序列化校验
    plugin_root = Path(__file__).resolve().parents[2] / "integrations" / "astrbot_plugin_anisignal"
    sys.path.insert(0, str(plugin_root))
    try:
        import configuration as astr_cfg

        astr_cfg.plugin_schema.cache_clear()
        plugin_target = astr_cfg.to_plugin_target({
            "name": "暗改群",
            "umo": "napcat:GroupMessage:999",
            "enabled": True,
            "revisions_enabled": True,
            "tools": ["digest", "revisions"],
        })
        assert plugin_target["notifications"]["revisions_enabled"] is True
        assert "revisions" in plugin_target["interaction"]["tools"]

        server_target = astr_cfg.to_server_target(plugin_target)
        assert server_target["revisions_enabled"] is True
        assert "revisions" in server_target["tools"]

        astr_cfg.apply_subscription([plugin_target], "napcat:GroupMessage:999", {"revisions_enabled": False})
        assert plugin_target["notifications"]["revisions_enabled"] is False
    finally:
        sys.path.remove(str(plugin_root))
