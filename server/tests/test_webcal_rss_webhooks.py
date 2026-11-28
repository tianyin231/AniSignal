"""Webcal 日历订阅、个人过滤 RSS 与通用用户 Webhook 测试。"""

import hashlib
import hmac
import json
import socket
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

import httpx
import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import (
    ContentSchedule,
    ContentVersion,
    EmailCode,
    Event,
    EventRevision,
    Evidence,
    NotificationDelivery,
    RawContent,
    Source,
    User,
    UserKeyword,
)
from app.services import webhooks as webhooks_service


async def _register_and_login(client, session, email="sub@t.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "password123"})
    code_row = await session.scalar(select(EmailCode).order_by(EmailCode.id.desc()).limit(1))
    resp = await client.post("/api/auth/verify", json={"email": email, "code": code_row.code_plain})
    assert resp.status_code == 200
    return await session.scalar(select(User).where(User.email == email))


@pytest.mark.asyncio
async def test_calendar_webcal_feed_all_and_single_community(session, client):
    now = utcnow()
    r1 = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="cal_1",
        url="https://mc.kurogames.com/notice/1",
        community="wuwa",
        content_hash="ch1",
        raw_json={},
    )
    r2 = RawContent(
        platform="miyoushe",
        content_type="notice",
        external_id="cal_2",
        url="https://www.miyoushe.com/ys/article/2",
        community="genshin",
        content_hash="ch2",
        raw_json={},
    )
    hidden_raw = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="cal_hidden",
        community="wuwa",
        hidden=True,
        content_hash="ch3",
        raw_json={},
    )
    session.add_all([r1, r2, hidden_raw])
    await session.flush()
    session.add_all([
        ContentSchedule(
            raw_content_id=r1.id,
            community="wuwa",
            title="2.4 前瞻特别通讯",
            kind="broadcast",
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=1),
            all_day=False,
        ),
        ContentSchedule(
            raw_content_id=r2.id,
            community="genshin",
            title="海灯节活动",
            kind="activity",
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=10),
            all_day=True,
        ),
        ContentSchedule(
            raw_content_id=hidden_raw.id,
            community="wuwa",
            title="隐藏测试活动",
            kind="activity",
            start_at=now + timedelta(days=1),
        ),
    ])
    await session.commit()

    # 全站订阅：带分区前缀、包含全部可见活动、排除隐藏内容
    resp_all = await client.get("/api/calendar.ics", params={"community": "all"})
    assert resp_all.status_code == 200
    assert "text/calendar" in resp_all.headers["content-type"]
    ics_all = resp_all.text
    assert "BEGIN:VCALENDAR" in ics_all
    assert "METHOD:PUBLISH" in ics_all
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT6H" in ics_all
    assert "[鸣潮] 2.4 前瞻特别通讯" in ics_all
    assert "[原神] 海灯节活动" in ics_all
    assert "隐藏测试活动" not in ics_all

    # 单分区订阅：只含本分区且无多余分区前缀
    resp_wuwa = await client.get("/api/calendar.ics", params={"community": "wuwa"})
    assert resp_wuwa.status_code == 200
    ics_wuwa = resp_wuwa.text
    assert "SUMMARY:2.4 前瞻特别通讯" in ics_wuwa
    assert "海灯节活动" not in ics_wuwa


@pytest.mark.asyncio
async def test_personal_rss_token_preferences_keywords_and_rotation(session, client):
    user = await _register_and_login(client, session, "rssuser@t.com")
    now = utcnow()
    r_wuwa = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="prss_1",
        community="wuwa",
        published_at=now - timedelta(hours=1),
        content_hash="pr1",
        raw_json={},
    )
    r_excluded = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="prss_2",
        community="wuwa",
        published_at=now - timedelta(hours=2),
        content_hash="pr2",
        raw_json={},
    )
    r_other_comm = RawContent(
        platform="miyoushe",
        content_type="notice",
        external_id="prss_3",
        community="genshin",
        published_at=now - timedelta(hours=3),
        content_hash="pr3",
        raw_json={},
    )
    session.add_all([r_wuwa, r_excluded, r_other_comm])
    await session.flush()
    session.add_all([
        ContentVersion(raw_content_id=r_wuwa.id, version=1, title="鸣潮 2.4 卡提希娅 PV", text="全新共鸣者预告"),
        ContentVersion(raw_content_id=r_excluded.id, version=1, title="鸣潮 二创抽奖活动", text="参与抽奖"),
        ContentVersion(raw_content_id=r_other_comm.id, version=1, title="原神 活动公告", text="活动详情"),
    ])
    user.feed_preferences = {"communities": ["wuwa"], "exclude_keywords": ["抽奖"]}
    session.add(UserKeyword(user_id=user.id, keyword="卡提希娅", enabled=True))
    await session.commit()

    tok_res = await client.get("/api/account/feed-token")
    assert tok_res.status_code == 200
    token1 = tok_res.json()["token"]
    assert len(token1) >= 24

    # 重复请求返回同一令牌
    assert (await client.get("/api/account/feed-token")).json()["token"] == token1

    # 个人偏好过滤模式：仅 wuwa 且排除「抽奖」
    rss_all = await client.get(f"/api/rss/personal/{token1}.xml")
    assert rss_all.status_code == 200
    assert "private" in rss_all.headers.get("cache-control", "")
    root = ET.fromstring(rss_all.text)
    titles = [el.findtext("title") for el in root.findall("./channel/item")]
    assert titles == ["鸣潮 2.4 卡提希娅 PV"]

    # 关键词模式
    rss_kw = await client.get(f"/api/rss/personal/{token1}.xml", params={"mode": "keywords"})
    assert rss_kw.status_code == 200
    kw_root = ET.fromstring(rss_kw.text)
    assert [el.findtext("title") for el in kw_root.findall("./channel/item")] == ["鸣潮 2.4 卡提希娅 PV"]

    # 令牌轮换后旧地址立即失效
    rot = await client.post("/api/account/feed-token/rotate")
    assert rot.status_code == 200
    token2 = rot.json()["token"]
    assert token2 != token1
    assert (await client.get(f"/api/rss/personal/{token1}.xml")).status_code == 404
    assert (await client.get(f"/api/rss/personal/{token2}.xml")).status_code == 200

    # 封禁用户后令牌失效
    user.status = "banned"
    await session.commit()
    assert (await client.get(f"/api/rss/personal/{token2}.xml")).status_code == 404


def test_webhook_provider_payload_builders():
    payload = {
        "event_id": 42,
        "title": "鸣潮 2.4 前瞻直播定档",
        "summary": "本周五 19:00 开启官方前瞻直播。",
        "community": "wuwa",
        "community_label": "鸣潮",
        "category": "version_update",
        "keywords": ["前瞻直播"],
        "url": "https://anisignal.xyz/events/42",
        "sources": [{"title": "官方公告", "source": "鸣潮官号", "url": "https://example.com/1"}],
    }

    # Bark
    url, headers, body = webhooks_service.build_webhook_request(
        "bark", "https://api.day.app/mykey", "tok123", payload, now_ts=1700000000
    )
    assert url == "https://api.day.app/mykey"
    assert headers["Authorization"] == "Bearer tok123"
    data = json.loads(body)
    assert data["title"] == "鸣潮 2.4 前瞻直播定档"
    assert data["group"] == "AniSignal"

    # Feishu + sign
    _, _, f_body = webhooks_service.build_webhook_request(
        "feishu", "https://open.feishu.cn/open-apis/bot/v2/hook/abc", "sec-f", payload, now_ts=1700000000
    )
    f_data = json.loads(f_body)
    assert f_data["msg_type"] == "interactive"
    assert f_data["timestamp"] == "1700000000"
    assert f_data["sign"]

    # DingTalk + query sign
    d_url, _, d_body = webhooks_service.build_webhook_request(
        "dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=xyz", "SEC123", payload, now_ts=1700000000
    )
    qs = parse_qs(urlsplit(d_url).query)
    assert qs["access_token"] == ["xyz"]
    assert qs["timestamp"] == ["1700000000000"]
    assert qs["sign"][0]
    assert json.loads(d_body)["msgtype"] == "markdown"

    # WeCom
    _, _, w_body = webhooks_service.build_webhook_request(
        "wecom", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k", None, payload, now_ts=1700000000
    )
    assert json.loads(w_body)["msgtype"] == "markdown"

    # Discord
    _, _, dc_body = webhooks_service.build_webhook_request(
        "discord", "https://discord.com/api/webhooks/1/a", None, payload, now_ts=1700000000
    )
    dc_data = json.loads(dc_body)
    assert dc_data["embeds"][0]["title"] == "鸣潮 2.4 前瞻直播定档"

    # ntfy（确保标题头全 ASCII 编码，不触发 httpx latin-1 报错）
    _, n_headers, n_body = webhooks_service.build_webhook_request(
        "ntfy", "https://ntfy.sh/anisignal-test", "ntfy-tok", payload, now_ts=1700000000
    )
    n_headers["Title"].encode("latin-1")
    assert n_headers["Authorization"] == "Bearer ntfy-tok"
    assert "前瞻直播" in n_body.decode("utf-8")

    # Generic JSON + HMAC-SHA256 验签
    _, g_headers, g_body = webhooks_service.build_webhook_request(
        "generic", "https://hooks.example.com/in", "my-secret", payload, now_ts=1700000000
    )
    expected_sig = hmac.new(
        b"my-secret", b"1700000000." + g_body, hashlib.sha256
    ).hexdigest()
    assert g_headers["X-AniSignal-Timestamp"] == "1700000000"
    assert g_headers["X-AniSignal-Signature"] == f"sha256={expected_sig}"


@pytest.mark.asyncio
async def test_webhook_ssrf_blocks_private_dns_and_redirects(monkeypatch):
    for bad in (
        "http://127.0.0.1:8000/hook",
        "http://localhost/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/internal",
        "http://user:pass@example.com/hook",
        "javascript:alert(1)",
    ):
        with pytest.raises(webhooks_service.WebhookError):
            webhooks_service.validate_webhook_url_static(bad)

    # DNS 解析到内网 IP（含 IPv4-mapped IPv6）必须拦截
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:127.0.0.1", 443, 0, 0))],
    )
    ok, code, err = await webhooks_service.send_webhook_http(
        "generic", "https://rebind.example.com/hook", None, {"title": "t"}
    )
    assert not ok and code is None and "私网" in (err or "")

    # 302 重定向到云元数据地址必须拦截
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )

    class _RedirectClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    monkeypatch.setattr(httpx, "AsyncClient", _RedirectClient)
    ok, code, err = await webhooks_service.send_webhook_http(
        "generic", "https://hooks.example.com/redir", None, {"title": "t"}
    )
    assert not ok and code == 302 and "重定向" in (err or "")


@pytest.mark.asyncio
async def test_webhook_account_crud_limit_test_and_delivery(session, client, monkeypatch):
    webhooks_service._TEST_LAST_AT.clear()
    user = await _register_and_login(client, session, "wh@t.com")

    # 内网地址创建被拒
    bad_resp = await client.post(
        "/api/account/webhooks",
        json={"name": "Bad", "provider": "generic", "url": "http://127.0.0.1/hook"},
    )
    assert bad_resp.status_code == 400

    # 正常创建 3 个达到上限，且 secret 不回显
    ids = []
    for idx in range(3):
        res = await client.post(
            "/api/account/webhooks",
            json={
                "name": f"通道{idx + 1}",
                "provider": "bark" if idx == 0 else "generic",
                "url": f"https://hooks.example.com/u/{idx}",
                "secret": "top-secret-value" if idx == 0 else None,
                "events_mode": "keywords" if idx == 0 else "all",
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert "secret" not in data
        assert data["has_secret"] is (idx == 0)
        ids.append(data["id"])

    # 第 4 个被上限拦截
    over = await client.post(
        "/api/account/webhooks",
        json={"name": "第4个", "provider": "bark", "url": "https://hooks.example.com/u/4"},
    )
    assert over.status_code == 409

    # 测试按钮 + 冷却保护
    sent_calls = []

    async def _fake_send(provider, url, secret, payload):
        sent_calls.append((provider, url, secret, payload))
        return True, 200, None

    monkeypatch.setattr(webhooks_service, "send_webhook_http", _fake_send)
    t1 = await client.post(f"/api/account/webhooks/{ids[0]}/test")
    assert t1.status_code == 200 and t1.json()["ok"] is True
    assert len(sent_calls) == 1
    assert sent_calls[0][2] == "top-secret-value"

    t2 = await client.post(f"/api/account/webhooks/{ids[0]}/test")
    assert t2.status_code == 429

    # 更新：清空密钥、停用第 3 个通道
    p = await client.patch(f"/api/account/webhooks/{ids[0]}", json={"clear_secret": True})
    assert p.status_code == 200 and p.json()["has_secret"] is False
    await client.delete(f"/api/account/webhooks/{ids[2]}")

    # 自动化事件推送：keywords 通道仅推命中词，all 通道推所有偏好分区重要事件
    user.feed_preferences = {"communities": ["wuwa"]}
    session.add(UserKeyword(user_id=user.id, keyword="卡提希娅", enabled=True))
    src = Source(platform="kuro_cn", external_id="kuro_official", name="鸣潮官号", identity="official")
    session.add(src)
    await session.flush()

    raw = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="wh_ev_raw_1",
        community="wuwa",
        source_id=src.id,
        published_at=utcnow(),
        content_hash="whh1",
        raw_json={},
    )
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, title="卡提希娅 2.4 版本前瞻公告", text="正文"))
    ev = Event(
        title="卡提希娅 2.4 版本前瞻公告",
        summary="官方公布 2.4 前瞻时间。",
        category="version_update",
        community="wuwa",
        identity="official",
        importance=True,
        is_active=True,
    )
    session.add(ev)
    await session.flush()
    session.add(Evidence(event_id=ev.id, raw_content_id=raw.id, role="support"))
    session.add(EventRevision(event_id=ev.id, actor="engine", note="新建官方重要事件"))
    await session.commit()

    sent_calls.clear()
    stats = await webhooks_service.run_webhook_notifications(session)
    assert stats == {"sent": 2, "failed": 0, "skipped": 0}
    assert len(sent_calls) == 2

    # 再次运行应幂等不重复发送
    stats2 = await webhooks_service.run_webhook_notifications(session)
    assert stats2 == {"sent": 0, "failed": 0, "skipped": 0}

    deliveries = list(
        await session.scalars(
            select(NotificationDelivery).where(NotificationDelivery.kind == "webhook")
        )
    )
    assert len(deliveries) == 2
    assert all(d.status == "sent" for d in deliveries)
