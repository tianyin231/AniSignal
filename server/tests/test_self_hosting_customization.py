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


async def test_issue3_target_validation_official_flag_runs_and_seeds_cli(client, session, admin_headers):
    from sqlalchemy import select
    from app.clock import utcnow
    from app.communities import reset_communities
    from app.models import CollectRun, RawContent, Source
    from app.services.ingest import ingest_items

    try:
        # F36: YouTube + keyword_search 在接口入口返回 422
        bad_yt = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={"platform": "youtube", "target_type": "keyword_search", "target_value": "wuthering waves"},
        )
        assert bad_yt.status_code == 422
        assert "频道 RSS" in bad_yt.text

        # 创建自定义分区并验证 F42/F38（全量同源跨分区返回 409 并指明已有分区；泛关键词+共现词综合频道允许跨分区分流）
        sec = await client.post(
            "/api/admin/communities",
            headers=admin_headers,
            json={"key": "ba", "label": "蔚蓝档案", "color": "sky"},
        )
        assert sec.status_code == 201

        chan = "UCabcdefghijklmnopqrstuv"
        t1 = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={
                "platform": "youtube",
                "target_type": "channel_rss",
                "target_value": chan,
                "community": "wuwa",
                "tier": "core",
            },
        )
        assert t1.status_code == 201 and t1.json()["is_official"] is False
        cross_dup = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={
                "platform": "youtube",
                "target_type": "channel_rss",
                "target_value": chan,
                "community": "ba",
                "tier": "core",
            },
        )
        assert cross_dup.status_code == 409 and "鸣潮" in cross_dup.text

        # 泛关键词 + 共现词的综合频道允许跨分区共享；若事后把其中一侧改为 core 则 409 拦截
        shared_chan = "UCsharedcreator0000000001"
        s1 = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={
                "platform": "youtube",
                "target_type": "channel_rss",
                "target_value": shared_chan,
                "community": "wuwa",
                "tier": "general",
                "co_terms": ["鸣潮"],
            },
        )
        s2 = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={
                "platform": "youtube",
                "target_type": "channel_rss",
                "target_value": shared_chan,
                "community": "ba",
                "tier": "general",
                "co_terms": ["蔚蓝档案"],
            },
        )
        assert s1.status_code == 201 and s2.status_code == 201
        hog = await client.put(
            f"/api/admin/collect-targets/{s1.json()['id']}",
            headers=admin_headers,
            json={"tier": "core"},
        )
        assert hog.status_code == 409

        # F40 + F46: 官方开关仅作用于目标账号自身（不误标转推作者），取消勾选后重置身份为 unknown
        ba_x = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={
                "platform": "x",
                "target_type": "account_dynamics",
                "target_value": "@EN_BlueArchive",
                "community": "ba",
                "interval_minutes": 60,
                "is_official": True,
            },
        )
        assert ba_x.status_code == 201 and ba_x.json()["is_official"] is True

        # F39: 运行记录按分区过滤，目标列表返回累计入库数（第 2 轮新增 0 时仍保留累计 7）
        session.add_all([
            CollectRun(target_id=t1.json()["id"], started_at=utcnow(), status="success", items_new=2),
            CollectRun(target_id=ba_x.json()["id"], started_at=utcnow(), status="success", items_new=7),
            CollectRun(target_id=ba_x.json()["id"], started_at=utcnow(), status="success", items_new=0),
        ])
        await session.commit()
        ba_runs = (await client.get("/api/admin/runs", params={"community": "ba"}, headers=admin_headers)).json()
        assert {r["target_id"] for r in ba_runs} == {ba_x.json()["id"]}
        ba_targets = (await client.get("/api/admin/collect-targets", params={"community": "ba"}, headers=admin_headers)).json()
        ba_x_row = next(r for r in ba_targets if r["id"] == ba_x.json()["id"])
        assert ba_x_row["total_items_new"] == 7

        stats = await ingest_items(
            session,
            "x_posts",
            [
                {
                    "id": "1900000000000000001",
                    "account": "en_bluearchive",
                    "author": "EN_BlueArchive",
                    "text": "Maintenance notice for Blue Archive.",
                    "url": "https://x.com/EN_BlueArchive/status/1900000000000000001",
                    "published_at": "2026-11-13T10:00:00+00:00",
                    "media": [],
                    "quoted_urls": [],
                },
                {
                    "id": "1900000000000000002",
                    "account": "en_bluearchive",
                    "author": "SomeFanArtist",
                    "text": "Fan art retweeted on official timeline.",
                    "url": "https://x.com/SomeFanArtist/status/1900000000000000002",
                    "published_at": "2026-11-13T10:05:00+00:00",
                    "media": [],
                    "quoted_urls": [],
                },
            ],
            community="ba",
            force_official="en_bluearchive",
        )
        assert stats.official_items == 1 and stats.community_items == 1
        raw_own = await session.scalar(select(RawContent).where(RawContent.external_id == "1900000000000000001"))
        raw_rt = await session.scalar(select(RawContent).where(RawContent.external_id == "1900000000000000002"))
        src_own = await session.get(Source, raw_own.source_id)
        src_rt = await session.get(Source, raw_rt.source_id)
        assert src_own.identity == "official" and src_rt.identity == "user"

        unset = await client.put(
            f"/api/admin/collect-targets/{ba_x.json()['id']}",
            headers=admin_headers,
            json={"is_official": False},
        )
        assert unset.status_code == 200 and unset.json()["is_official"] is False
        await session.refresh(src_own)
        assert src_own.identity == "unknown"

        # 重新勾选 is_official 时立即同步存量 Source.identity，无需等下轮抓取
        reset_on = await client.put(
            f"/api/admin/collect-targets/{ba_x.json()['id']}",
            headers=admin_headers,
            json={"is_official": True},
        )
        assert reset_on.status_code == 200 and reset_on.json()["is_official"] is True
        await session.refresh(src_own)
        assert src_own.identity == "official"

        # 同账号双目标（dynamics + videos）：取消其中一个不冲掉另一个的官方身份
        bili_dyn = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={"platform": "bilibili", "target_type": "account_dynamics", "target_value": "99887766", "community": "ba", "is_official": True},
        )
        bili_vid = await client.post(
            "/api/admin/collect-targets",
            headers=admin_headers,
            json={"platform": "bilibili", "target_type": "account_videos", "target_value": "99887766", "community": "ba", "is_official": True},
        )
        await ingest_items(
            session,
            "bilibili_dynamics",
            [{"id": "998877660001", "author_mid": "99887766", "author_name": "BA官号", "text": "BA PV"}],
            community="ba",
            force_official="99887766",
        )
        bili_src = await session.scalar(select(Source).where(Source.platform == "bilibili", Source.external_id == "99887766"))
        assert bili_src.identity == "official"
        await client.put(f"/api/admin/collect-targets/{bili_dyn.json()['id']}", headers=admin_headers, json={"is_official": False})
        await session.refresh(bili_src)
        assert bili_src.identity == "official"
        await client.delete(f"/api/admin/collect-targets/{bili_vid.json()['id']}", headers=admin_headers)
        await session.refresh(bili_src)
        assert bili_src.identity == "unknown"

        # RSS 勾选官方一手：editorial_tier 保持 None，以便 news_editorial 按 identity == official 识别为一手来源
        from app.collectors.rss import rss_id

        feed_url = "https://bluearchive.example.com/rss.xml"
        await ingest_items(
            session,
            "rss_feed",
            [{
                "feed_url": feed_url,
                "source_name": "BA Official Blog",
                "title": "Official Patch Notes",
                "url": "https://bluearchive.example.com/posts/1",
                "text": "Patch details",
                "published": "2026-11-13T10:00:00Z",
                "tier": "T2",
            }],
            community="ai",
            force_official=rss_id(feed_url),
        )
        rss_src = await session.scalar(select(Source).where(Source.platform == "rss", Source.external_id == rss_id(feed_url)))
        assert rss_src.identity == "official" and rss_src.editorial_tier is None
    finally:
        reset_communities()


def test_seeds_cli_all_flag(monkeypatch):
    import sys
    from app.seeds import __main__ as seeds_cli

    called = []

    async def fake_run(selected):
        called.append(selected)

    monkeypatch.setattr(seeds_cli, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["app.seeds", "--all"])
    seeds_cli.main()
    assert called == [None]


async def test_builtin_edit_delete_restore_hex_palette_and_schedule_gating(client, admin_headers, session):
    from app.communities import reset_communities
    from app.models import CollectTarget
    from app.services.collect import load_due_targets

    try:
        bad_color = await client.post(
            "/api/admin/communities",
            headers=admin_headers,
            json={"key": "steam", "label": "Steam", "color": "#12345"},
        )
        assert bad_color.status_code == 422

        created = await client.post(
            "/api/admin/communities",
            headers=admin_headers,
            json={"key": "steam", "label": "Steam 独游", "en": "Steam Indie", "color": "#EF4444"},
        )
        assert created.status_code == 201
        assert created.json()["color"] == "#ef4444"

        edited_builtin = await client.put(
            "/api/admin/communities/blhx",
            headers=admin_headers,
            json={"label": "碧蓝航线定制", "color": "#10B981"},
        )
        assert edited_builtin.status_code == 200
        assert edited_builtin.json()["label"] == "碧蓝航线定制"
        assert edited_builtin.json()["color"] == "#10b981"

        # 删除默认内置分区 wuwa 后，缺省 ?community= 自动回退到首个启用分区，不再 500/KeyError
        del_wuwa = await client.delete("/api/admin/communities/wuwa", headers=admin_headers)
        assert del_wuwa.status_code == 204

        admin_list = (await client.get("/api/admin/communities", headers=admin_headers)).json()
        assert "wuwa" not in {c["key"] for c in admin_list["items"]}
        assert "wuwa" in {c["key"] for c in admin_list["deleted_builtins"]}

        default_feed = await client.get("/api/contents")
        assert default_feed.status_code == 200
        assert (await client.get("/api/contents", params={"community": "wuwa"})).status_code == 422

        # 隐藏 hsr 后，到期采集目标不再调度，且 /?community=all 不混入隐藏分区内容
        await client.put("/api/admin/communities/hsr", headers=admin_headers, json={"active": False})
        session.add_all([
            CollectTarget(platform="rss", target_type="feed", target_value="https://example.com/hsr.xml", community="hsr", enabled=True),
            CollectTarget(platform="rss", target_type="feed", target_value="https://example.com/steam.xml", community="steam", enabled=True),
        ])
        now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        await ingest_items(
            session,
            "rss_feed",
            [{"url": "https://example.com/hsr/1", "feed_url": "https://example.com/hsr.xml", "title": "隐藏分区动态", "published": now_rfc}],
            community="hsr",
        )
        await ingest_items(
            session,
            "rss_feed",
            [{"url": "https://example.com/steam/1", "feed_url": "https://example.com/steam.xml", "title": "Steam 新品节开启", "published": now_rfc}],
            community="steam",
        )
        await session.commit()

        from app.clock import utcnow

        due_communities = {t.community for t in await load_due_targets(session, utcnow())}
        assert "steam" in due_communities
        assert "hsr" not in due_communities

        all_feed = (await client.get("/api/contents", params={"community": "all"})).json()
        titles = [item["title"] for item in all_feed["items"]]
        assert "Steam 新品节开启" in titles
        assert "隐藏分区动态" not in titles

        # 批量移除空内置分区 + 恢复预设分区（手动 batch enable 与导入预设包自动恢复）
        batch_del = await client.post(
            "/api/admin/communities/batch",
            headers=admin_headers,
            json={"keys": ["ai", "zzz"], "action": "delete"},
        )
        assert batch_del.status_code == 200
        assert {"wuwa", "ai", "zzz"} <= {d["key"] for d in batch_del.json()["deleted_builtins"]}

        restore_wuwa = await client.post(
            "/api/admin/communities/batch",
            headers=admin_headers,
            json={"keys": ["wuwa"], "action": "enable"},
        )
        assert restore_wuwa.status_code == 200
        assert "wuwa" in {c["key"] for c in restore_wuwa.json()["items"] if c["active"]}

        imp_ai = await client.post(
            "/api/admin/collect-targets/import-presets",
            headers=admin_headers,
            json={"communities": ["ai"]},
        )
        assert imp_ai.status_code == 200
        cfg = (await client.get("/api/site-config")).json()
        assert "ai" in {c["key"] for c in cfg["communities"]}
    finally:
        reset_communities()


async def test_user_custom_feeds_rss_youtube_ssrf_and_manual_collect(client, admin_headers, session, monkeypatch):
    from app.collectors.rss import RssCollector
    from app.collectors.youtube_api import YoutubeApiCollector
    from app.models.enums import Platform
    from app.schemas.content import FetchResult
    from app.services.custom_feeds import sweep_custom_feeds

    # 创建并激活测试用户，开启全员公测
    await client.post("/api/auth/register", json={"email": "custom@example.com", "password": "password123"})
    uid = (await client.get("/api/admin/users", headers=admin_headers)).json()["items"][0]["id"]
    await client.post(f"/api/admin/users/{uid}/verify", headers=admin_headers)

    cap = (await client.get("/api/admin/custom-feeds/capacity", headers=admin_headers)).json()["policy"]
    cap["open_beta"] = True
    await client.put("/api/admin/custom-feeds/policy", headers=admin_headers, json=cap)

    login = await client.post("/api/auth/login", json={"email": "custom@example.com", "password": "password123"})
    assert login.status_code == 200

    # SSRF 内网地址被拒绝
    ssrf_resp = await client.post(
        "/api/account/custom-feeds",
        json={"name": "内网探测", "kind": "feed", "platform": "rss", "target_value": "http://127.0.0.1:8080/feed.xml"},
    )
    assert ssrf_resp.status_code == 400

    async def _allow_public_url(url: str) -> str:
        return url

    monkeypatch.setattr("app.collectors.rss.public_feed_url", _allow_public_url)

    rss_create = await client.post(
        "/api/account/custom-feeds",
        json={
            "name": "独立游戏博客",
            "kind": "feed",
            "platform": "rss",
            "target_value": "https://indie.example.com/feed.xml",
            "is_public": True,
        },
    )
    assert rss_create.status_code == 201
    rss_feed_id = rss_create.json()["id"]

    yt_create = await client.post(
        "/api/account/custom-feeds",
        json={
            "name": "官方频道跟踪",
            "kind": "channel_rss",
            "platform": "youtube",
            "target_value": "https://www.youtube.com/channel/UC1234567890123456789012/videos?view=0",
            "is_public": True,
        },
    )
    assert yt_create.status_code == 201
    yt_feed_id = yt_create.json()["id"]

    yt_detail = (await client.get(f"/api/custom/feeds/{yt_feed_id}")).json()
    assert yt_detail["target_value"] == "UC1234567890123456789012"
    assert yt_detail["source_url"] == "https://www.youtube.com/channel/UC1234567890123456789012"

    # 手动触发抓取成功（首次未采集过）
    trig_ok = await client.post(f"/api/account/custom-feeds/{rss_feed_id}/collect")
    assert trig_ok.status_code == 202

    # 真实跑通 sweep_custom_feeds：同时覆盖 RSS 与 YouTube 频道抓取与分发落库
    now_iso = datetime.now(timezone.utc).isoformat()

    async def _fake_rss_fetch(self, target_type: str, feed_url: str, limit: int):
        return FetchResult(
            platform=Platform.RSS,
            fetch_kind="rss_feed",
            items=[{
                "id": "rss_item_1",
                "feed_url": feed_url,
                "source_name": "Indie Blog",
                "author": "Team Cherry",
                "title": "Hollow Silksong Devlog",
                "url": "https://indie.example.com/posts/silksong",
                "text": "Release window announced.",
                "html": "<p>Release window announced.</p>",
                "published": now_iso,
                "media": [],
            }],
        )

    async def _fake_yt_fetch(self, target_type: str, channel_id: str, limit: int):
        return FetchResult(
            platform=Platform.YOUTUBE,
            fetch_kind="youtube_rss",
            items=[{
                "id": "dQw4w9WgXcQ",
                "channel_id": channel_id,
                "title": "Official Launch Trailer",
                "link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "published": now_iso,
                "updated": now_iso,
                "author": "Indie Studio",
                "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
                "summary": "Trailer out now.",
            }],
        )

    from app.services.queue import LocalRedis

    monkeypatch.setattr(RssCollector, "fetch", _fake_rss_fetch)
    monkeypatch.setattr(YoutubeApiCollector, "fetch", _fake_yt_fetch)

    stats = await sweep_custom_feeds(session, LocalRedis())
    assert stats["sources"] == 2
    assert stats["new_items"] == 2

    rss_detail = (await client.get(f"/api/custom/feeds/{rss_feed_id}")).json()
    assert rss_detail["total"] == 1
    assert rss_detail["items"][0]["title"] == "Hollow Silksong Devlog"
    assert rss_detail["items"][0]["platform"] == "rss"

    yt_after = (await client.get(f"/api/custom/feeds/{yt_feed_id}")).json()
    assert yt_after["total"] == 1
    assert yt_after["items"][0]["title"] == "Official Launch Trailer"
    assert yt_after["items"][0]["platform"] == "youtube"

    # 刚采集完 5 分钟冷却内再次手动触发返回 429
    trig_cooldown = await client.post(f"/api/account/custom-feeds/{rss_feed_id}/collect")
    assert trig_cooldown.status_code == 429


async def test_delete_community_with_purge_resyncs_official_source(client, admin_headers, session):
    """F48 顺带：删除分区时连带删掉的官方目标，也要立即撤销来源的官方身份。"""
    from sqlalchemy import select

    from app.models import Source

    created = await client.post(
        "/api/admin/communities", headers=admin_headers,
        json={"key": "tmpofc", "label": "临时分区", "color": "sky"},
    )
    assert created.status_code == 201
    target = await client.post(
        "/api/admin/collect-targets", headers=admin_headers,
        json={
            "platform": "x", "target_type": "account_dynamics", "target_value": "someofficial",
            "display_name": "某官方", "interval_minutes": 60, "community": "tmpofc", "is_official": True,
        },
    )
    assert target.status_code == 201, target.text
    session.add(Source(platform="x", external_id="someofficial", name="某官方", identity="official"))
    await session.commit()

    resp = await client.delete(
        "/api/admin/communities/tmpofc", params={"purge_targets": "true"}, headers=admin_headers,
    )
    assert resp.status_code == 204
    session.expire_all()
    source = await session.scalar(select(Source).where(Source.external_id == "someofficial"))
    assert source.identity == "unknown"

    # 批量接口（目标启停/删除、分区批量删除）同样要立即同步 Source 官方身份。
    created2 = await client.post(
        "/api/admin/communities", headers=admin_headers,
        json={"key": "tmpofc2", "label": "临时分区二", "color": "#0ea5e9"},
    )
    assert created2.status_code == 201
    target2 = await client.post(
        "/api/admin/collect-targets", headers=admin_headers,
        json={
            "platform": "x", "target_type": "account_dynamics", "target_value": "someofficial",
            "display_name": "某官方", "interval_minutes": 60, "community": "tmpofc2", "is_official": True,
        },
    )
    assert target2.status_code == 201
    tid2 = target2.json()["id"]
    session.expire_all()
    assert (await session.scalar(select(Source).where(Source.external_id == "someofficial"))).identity == "official"

    await client.post("/api/admin/collect-targets/batch", headers=admin_headers, json={"action": "disable", "ids": [tid2]})
    session.expire_all()
    assert (await session.scalar(select(Source).where(Source.external_id == "someofficial"))).identity == "unknown"

    await client.post("/api/admin/collect-targets/batch", headers=admin_headers, json={"action": "enable", "ids": [tid2]})
    session.expire_all()
    assert (await session.scalar(select(Source).where(Source.external_id == "someofficial"))).identity == "official"

    batch_del = await client.post(
        "/api/admin/communities/batch", headers=admin_headers,
        json={"action": "delete", "keys": ["tmpofc2"], "purge_targets": True},
    )
    assert batch_del.status_code == 200
    session.expire_all()
    assert (await session.scalar(select(Source).where(Source.external_id == "someofficial"))).identity == "unknown"
