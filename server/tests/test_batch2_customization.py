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


async def test_unified_admin_session_cookie_and_csrf_guards(client, admin_headers):
    # 普通账号注册、激活并登录：默认不具备管理员权限
    await client.post("/api/auth/register", json={"email": "owner@example.com", "password": "password123"})
    await client.post("/api/auth/register", json={"email": "member@example.com", "password": "password123"})
    users = {u["email"]: u for u in (await client.get("/api/admin/users", headers=admin_headers)).json()["items"]}
    owner_id = users["owner@example.com"]["id"]
    member_id = users["member@example.com"]["id"]
    await client.post(f"/api/admin/users/{owner_id}/verify", headers=admin_headers)
    await client.post(f"/api/admin/users/{member_id}/verify", headers=admin_headers)

    login_resp = await client.post("/api/auth/login", json={"email": "owner@example.com", "password": "password123"})
    assert login_resp.status_code == 200
    assert (await client.get("/api/auth/session")).json()["user"]["is_admin"] is False
    assert (await client.get("/api/admin/collect-targets")).status_code == 401

    # 凭 Token 一键绑定当前已登录账号为管理员后，免 Header 可直接访问管理后台
    bind_resp = await client.post("/api/admin/users/bind-self", headers=admin_headers)
    assert bind_resp.status_code == 200
    assert bind_resp.json()["is_admin"] is True
    assert (await client.get("/api/auth/session")).json()["user"]["is_admin"] is True
    assert (await client.get("/api/admin/collect-targets")).status_code == 200

    # 显式携带错误 X-Admin-Token 时不得回落到会话 Cookie（确保 /admin/login 校验准确）
    assert (await client.get("/api/admin/collect-targets", headers={"X-Admin-Token": "wrong"})).status_code == 401

    # Cookie 鉴权下的跨站写操作被拦截，同源写操作放行
    csrf_site = await client.patch(
        f"/api/admin/users/{member_id}",
        headers={"Sec-Fetch-Site": "cross-site"},
        json={"is_admin": True},
    )
    assert csrf_site.status_code == 401
    csrf_origin = await client.patch(
        f"/api/admin/users/{member_id}",
        headers={"Origin": "https://evil.example"},
        json={"is_admin": True},
    )
    assert csrf_origin.status_code == 401

    grant = await client.patch(
        f"/api/admin/users/{member_id}",
        headers={"Origin": "http://test"},
        json={"is_admin": True},
    )
    assert grant.status_code == 200
    assert grant.json()["is_admin"] is True

    # 当前 Cookie 会话禁止自我撤权或自我封禁，防止误操作锁死后台
    self_demote = await client.patch(f"/api/admin/users/{owner_id}", json={"is_admin": False})
    assert self_demote.status_code == 400
    self_ban = await client.patch(f"/api/admin/users/{owner_id}", json={"banned": True})
    assert self_ban.status_code == 400

    # 封禁其他管理员会自动吊销其管理员权限
    ban_member = await client.patch(f"/api/admin/users/{member_id}", json={"banned": True, "ban_reason": "test"})
    assert ban_member.status_code == 200
    assert ban_member.json()["is_admin"] is False
    assert "admin_revoked" in ban_member.json()["actions"]


async def test_raw_ai_news_respects_manual_withdrawal_and_visibility(client, session, admin_headers, monkeypatch):
    from datetime import datetime, timezone
    from app.config import get_settings
    from app.models import ContentReview, ContentVersion, RawContent
    from app.services.ingest import ingest_items
    from sqlalchemy import select

    monkeypatch.setattr(get_settings(), "news_unified_enabled", True)
    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    await ingest_items(
        session,
        "rss_feed",
        [
            {
                "url": "https://openai.com/index/unreviewed-post/",
                "feed_url": "https://openai.com/news/rss.xml",
                "title": "Unreviewed Raw Post",
                "text": "<p>Unreviewed body.</p>",
                "source_name": "OpenAI News",
                "published": now_rfc,
                "first_party": True,
            },
            {
                "url": "https://anthropic.com/news/reviewed-post/",
                "feed_url": "https://anthropic.com/rss.xml",
                "title": "Reviewed Approved Post",
                "text": "<p>Approved body.</p>",
                "source_name": "Anthropic",
                "published": now_rfc,
                "first_party": True,
            },
        ],
        community="ai",
    )
    await session.commit()

    rows = list((await session.scalars(select(RawContent).order_by(RawContent.id.asc()))).all())
    unreviewed_raw, reviewed_raw = rows[0], rows[1]
    rev_ver = await session.scalar(
        select(ContentVersion.version).where(ContentVersion.raw_content_id == reviewed_raw.id)
    )
    reviewed_raw.hidden = False
    session.add(
        ContentReview(
            raw_content_id=reviewed_raw.id,
            model="test",
            relevant=True,
            promotional=False,
            violation=False,
            quality_score=85,
            news={
                "content_hash": reviewed_raw.content_hash,
                "content_version": rev_ver,
                "newsworthy": True,
                "headline": "已过审快讯标题",
                "summary": "已过审摘要",
                "kind": "report",
                "topics": ["Claude"],
            },
        )
    )
    await session.commit()

    # 两条在 include_raw=true 下初始均可见
    feed0 = (await client.get("/api/ai-news/events", params={"include_raw": "true"})).json()
    assert feed0["total"] == 2

    # 1) 撤下已过审条目（/api/admin/aihot/content/.../visibility）：include_raw 与详情均不得再泄露
    withdraw = await client.post(
        f"/api/admin/aihot/content/as-{reviewed_raw.id}/visibility",
        headers=admin_headers,
        json={"visibility": "withdrawn", "version": rev_ver, "reason": "事实核查撤稿"},
    )
    assert withdraw.status_code == 200
    feed1 = (await client.get("/api/ai-news/events", params={"include_raw": "true"})).json()
    assert [it["id"] for it in feed1["items"]] == [unreviewed_raw.id]
    assert (await client.get(f"/api/ai-news/engine/items/raw-{reviewed_raw.id}")).status_code == 404

    # 2) 下架未审条目（/api/admin/contents/.../visibility）：同样从原文模式消失，恢复后重新出现
    hide_unrev = await client.patch(
        f"/api/admin/contents/{unreviewed_raw.id}/visibility",
        headers=admin_headers,
        json={"hidden": True},
    )
    assert hide_unrev.status_code == 200
    feed2 = (await client.get("/api/ai-news/events", params={"include_raw": "true"})).json()
    assert feed2["total"] == 0
    assert (await client.get(f"/api/ai-news/engine/items/raw-{unreviewed_raw.id}")).status_code == 404

    restore_unrev = await client.patch(
        f"/api/admin/contents/{unreviewed_raw.id}/visibility",
        headers=admin_headers,
        json={"hidden": False},
    )
    assert restore_unrev.status_code == 200
    feed3 = (await client.get("/api/ai-news/events", params={"include_raw": "true"})).json()
    assert [it["id"] for it in feed3["items"]] == [unreviewed_raw.id]
    assert (await client.get(f"/api/ai-news/engine/items/raw-{unreviewed_raw.id}")).status_code == 200


async def test_multiprocess_custom_community_lazy_sync(client, admin_headers):
    from app.communities import COMMUNITIES, reset_communities

    try:
        created = await client.post(
            "/api/admin/communities",
            headers=admin_headers,
            json={"key": "gfl2", "label": "少女前线2", "color": "orange"},
        )
        assert created.status_code == 201

        # 模拟另一台尚未加载新分区的 API / Collector 副本进程
        reset_communities()
        assert "gfl2" not in COMMUNITIES

        # 直接请求公开接口与后台目标写入，均应自动从 DB 同步分区而不报 422
        events_resp = await client.get("/api/events", params={"community": "gfl2"})
        assert events_resp.status_code == 200
        assert "gfl2" in COMMUNITIES

        reset_communities()
        target_resp = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={
                "platform": "rss",
                "target_type": "feed",
                "target_value": "https://example.com/gfl2.xml",
                "display_name": "少前2资讯",
                "community": "gfl2",
            },
        )
        assert target_resp.status_code == 201
    finally:
        reset_communities()
