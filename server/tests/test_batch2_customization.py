"""Batch 2 定制化与零 SSH 闭环回归测试：
自定义分区、一键预设导入与批量清理、无 AI 原文直出、B 站后台扫码/Cookie、无 SMTP 激活/改密与发信限流。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx

from app.collectors.bilibili import BilibiliCollector
from app.services import settings as settings_service
from app.services.ingest import ingest_items


async def test_custom_community_lifecycle_and_direct_feed(client, admin_headers, session):
    # 1. 新建自定义分区 bluearchive
    create_resp = await client.post(
        "/api/admin/communities",
        headers=admin_headers,
        json={
            "key": "bluearchive",
            "label": "蔚蓝档案",
            "en": "Blue Archive",
            "color": "sky",
            "official_bili_mid": "3493265644980448",
            "aliases": ["BA", "碧蓝档案"],
        },
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["key"] == "bluearchive"
    assert created["domain"] == "game"
    assert created["builtin"] is False

    # 2. 批量隐藏部分内置分区，验证 site-config 实时反映
    batch_resp = await client.post(
        "/api/admin/communities/batch",
        headers=admin_headers,
        json={"keys": ["blhx", "hsr"], "action": "disable"},
    )
    assert batch_resp.status_code == 200

    cfg_resp = await client.get("/api/site-config")
    assert cfg_resp.status_code == 200
    cfg = cfg_resp.json()
    active_keys = [c["key"] for c in cfg["communities"]]
    all_keys = [c["key"] for c in cfg["all_communities"]]
    assert "bluearchive" in active_keys
    assert "blhx" not in active_keys
    assert "blhx" in all_keys
    assert cfg["ai_configured"] is False
    assert cfg["smtp_configured"] is False

    # 3. 为自定义分区创建 RSS 采集源并入库内容，验证无 AI 下公开列表立即可见
    target_resp = await client.post(
        "/api/admin/collect-targets",
        headers=admin_headers,
        json={
            "platform": "rss",
            "target_type": "feed",
            "target_value": "https://example.com/ba/rss.xml",
            "display_name": "蔚蓝档案动态源",
            "interval_minutes": 30,
            "community": "bluearchive",
        },
    )
    assert target_resp.status_code == 201

    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    await ingest_items(
        session,
        "rss_feed",
        [
            {
                "url": "https://example.com/ba/post-1",
                "feed_url": "https://example.com/ba/rss.xml",
                "title": "蔚蓝档案新版本预告",
                "text": "全新活动即将开启",
                "source_name": "蔚蓝档案动态源",
                "published": now_rfc,
                "first_party": True,
            }
        ],
        community="bluearchive",
    )
    await session.commit()

    feed_resp = await client.get("/api/contents", params={"community": "bluearchive"})
    assert feed_resp.status_code == 200
    feed = feed_resp.json()
    assert feed["total"] == 1
    assert feed["items"][0]["title"] == "蔚蓝档案新版本预告"

    # 4. 有目标时直接删除返回 409；带 purge_targets=true 成功删除
    del_conflict = await client.delete("/api/admin/communities/bluearchive", headers=admin_headers)
    assert del_conflict.status_code == 409

    del_ok = await client.delete(
        "/api/admin/communities/bluearchive",
        params={"purge_targets": "true"},
        headers=admin_headers,
    )
    assert del_ok.status_code == 204


async def test_presets_import_and_batch_target_management(client, admin_headers):
    presets_resp = await client.get("/api/admin/collect-targets/presets", headers=admin_headers)
    assert presets_resp.status_code == 200
    presets = presets_resp.json()["items"]
    by_key = {p["community"]: p for p in presets}
    assert "wuwa" in by_key and by_key["wuwa"]["count"] >= 4
    assert "ai" in by_key and "rss" in by_key["ai"]["platforms"]

    # 一键导入 wuwa 与 ai 预设（幂等）
    imp1 = await client.post(
        "/api/admin/collect-targets/import-presets",
        headers=admin_headers,
        json={"communities": ["wuwa", "ai"]},
    )
    assert imp1.status_code == 200
    first_added = imp1.json()["added"]
    assert first_added >= 10

    imp2 = await client.post(
        "/api/admin/collect-targets/import-presets",
        headers=admin_headers,
        json={"communities": ["wuwa", "ai"]},
    )
    assert imp2.status_code == 200
    assert imp2.json()["added"] == 0
    assert imp2.json()["skipped"] == first_added

    # 验证预设中的 X 目标默认关闭，RSS 目标默认开启
    ai_targets = (await client.get("/api/admin/collect-targets", params={"community": "ai"}, headers=admin_headers)).json()
    assert any(t["platform"] == "rss" and t["enabled"] is True for t in ai_targets)
    assert all(t["enabled"] is False for t in ai_targets if t["platform"] == "x")

    # 批量停用 / 启用 / 按分区一键移除
    ids = [ai_targets[0]["id"], ai_targets[1]["id"]]
    dis = await client.post(
        "/api/admin/collect-targets/batch",
        headers=admin_headers,
        json={"ids": ids, "action": "disable"},
    )
    assert dis.status_code == 200
    assert dis.json()["affected"] == 2

    purge = await client.post(
        "/api/admin/collect-targets/batch",
        headers=admin_headers,
        json={"community": "wuwa", "action": "delete"},
    )
    assert purge.status_code == 200
    assert purge.json()["affected"] == by_key["wuwa"]["count"]


async def test_ai_news_raw_fallback_and_detail_route(client, session):
    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    await ingest_items(
        session,
        "rss_feed",
        [
            {
                "url": "https://openai.com/index/gpt-next/",
                "feed_url": "https://openai.com/news/rss.xml",
                "title": "Introducing GPT-Next",
                "text": "<p>Faster reasoning and native multimodal tool use.</p>",
                "source_name": "OpenAI News",
                "published": now_rfc,
                "first_party": True,
            }
        ],
        community="ai",
    )
    await session.commit()

    # 默认不含未审原文（保持既有精炼快讯语义）
    strict_resp = await client.get("/api/ai-news")
    assert strict_resp.status_code == 200
    assert strict_resp.json()["total"] == 0

    # include_raw=true 直接返回原文条目与 raw-{id} 详情路由
    raw_resp = await client.get("/api/ai-news/events", params={"include_raw": "true"})
    assert raw_resp.status_code == 200
    payload = raw_resp.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["headline"] == "Introducing GPT-Next"
    assert item["verification"] == "unreviewed"
    assert item["path"].startswith("/ai/items/raw-")

    raw_id = item["path"].rsplit("/", 1)[-1]
    detail_resp = await client.get(f"/api/ai-news/engine/items/{raw_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["title"] == "Introducing GPT-Next"
    assert "Faster reasoning" in detail["body"]["original"]


async def test_bilibili_qr_cookie_and_env_injection(client, admin_headers, monkeypatch):
    from app.services import bilibili_session

    class _FakeResp:
        def __init__(self, data: dict):
            self._data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._data

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.cookies = httpx.Cookies()
            self.cookies.set("SESSDATA", "qr_sess_123", domain=".bilibili.com")
            self.cookies.set("bili_jct", "csrf_456", domain=".bilibili.com")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url: str, params: dict | None = None):
            if url.endswith("generate"):
                return _FakeResp({"code": 0, "data": {"url": "https://passport.bilibili.com/h5-app/passport/login/scan", "qrcode_key": "qrkey_abc123"}})
            if url.endswith("poll"):
                return _FakeResp({"code": 0, "data": {"code": 0}})
            return _FakeResp({"code": 0, "data": {"isLogin": True, "uname": "AniAdmin"}})

    monkeypatch.setattr(bilibili_session.httpx, "AsyncClient", _FakeAsyncClient)

    qr_resp = await client.post("/api/admin/bilibili-session/qrcode", headers=admin_headers)
    assert qr_resp.status_code == 200
    assert qr_resp.json()["qrcode_key"] == "qrkey_abc123"
    assert qr_resp.json()["qr_image"].startswith("data:image/png;base64,")

    poll_resp = await client.get("/api/admin/bilibili-session/qrcode/qrkey_abc123", headers=admin_headers)
    assert poll_resp.status_code == 200
    assert poll_resp.json()["state"] == "saved"

    cookie_resp = await client.put(
        "/api/admin/bilibili-session/cookie",
        headers=admin_headers,
        json={"cookie": "SESSDATA=manual_sess_789; bili_jct=manual_csrf"},
    )
    assert cookie_resp.status_code == 200
    assert cookie_resp.json()["state"] == "logged_in"

    # 验证采集器使用内存 BILI_COOKIE 环境变量而非只读文件参数
    collector = BilibiliCollector()
    collector.set_cookies({"SESSDATA": "manual_sess_789", "bili_jct": "manual_csrf"})
    captured: dict = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return json.dumps([]).encode(), b""

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env") or {}
        return _Proc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
    await collector._run_once("search", "鸣潮")
    assert "--cookie-file" not in captured["args"]
    assert "SESSDATA=manual_sess_789" in captured["env"].get("BILI_COOKIE", "")

    del_resp = await client.delete("/api/admin/bilibili-session", headers=admin_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["state"] in ("unconfigured", "missing")


async def test_no_smtp_user_verify_reset_mail_cap_and_ai_test_fix(client, admin_headers, session, monkeypatch):
    # 1. 无 SMTP 注册 -> 管理员查看明文验证码 -> 管理员直接激活 & 重置密码
    reg = await client.post("/api/auth/register", json={"email": "player@example.com", "password": "oldpassword123"})
    assert reg.status_code in (200, 201)

    codes = (await client.get("/api/admin/email-codes", headers=admin_headers)).json()
    assert len(codes) == 1
    assert codes[0]["email"] == "player@example.com"
    assert codes[0]["code"] and len(codes[0]["code"]) == 6

    users = (await client.get("/api/admin/users", headers=admin_headers)).json()["items"]
    uid = users[0]["id"]
    assert users[0]["is_verified"] is False

    ver = await client.post(f"/api/admin/users/{uid}/verify", headers=admin_headers)
    assert ver.status_code == 200
    assert ver.json()["is_verified"] is True

    rst = await client.post(
        f"/api/admin/users/{uid}/reset-password",
        headers=admin_headers,
        json={"password": "newpassword456"},
    )
    assert rst.status_code == 200

    login = await client.post("/api/auth/login", json={"email": "player@example.com", "password": "newpassword456"})
    assert login.status_code == 200

    # 2. 配置 SMTP 后全局每小时发信上限保护
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "mail_hourly_limit", 1)
    await settings_service.set_setting(
        session,
        "smtp",
        {"host": "smtp.example.com", "port": 587, "user": "bot", "password": "secret", "from": "bot@example.com", "tls_mode": "starttls"},
    )
    monkeypatch.setattr("app.services.auth.send_email", lambda *a, **kw: _async_true())

    r1 = await client.post("/api/auth/register", json={"email": "u1@example.com", "password": "password123"})
    assert r1.status_code in (200, 201)
    r2 = await client.post("/api/auth/register", json={"email": "u2@example.com", "password": "password123"})
    assert r2.status_code == 429

    # 3. /api/admin/settings/ai/test 解包修复验证
    await settings_service.set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "api_key": "sk-test", "model": "gpt-test"},
    )

    async def _fake_chat(*args, **kwargs):
        return ("正常", {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})

    monkeypatch.setattr("app.services.ai.chat", _fake_chat)
    ai_test = await client.post("/api/admin/settings/ai/test", headers=admin_headers)
    assert ai_test.status_code == 200
    assert ai_test.json() == {"ok": True, "message": "模型响应：正常"}


async def _async_true() -> bool:
    return True
