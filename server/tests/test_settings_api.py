"""站点配置 API 单测：SMTP 脱敏与保留密码、公告开关、文案白名单、公开端点。"""


async def test_smtp_masked_and_password_kept(client, admin_headers):
    # 保存 SMTP（带密码）
    resp = await client.put(
        "/api/admin/settings/smtp",
        json={"host": "smtp.example.com", "port": 587, "user": "noreply@example.com",
              "password": "secret-code", "from": "AniSignal <noreply@example.com>"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["host"] == "smtp.example.com"
    assert "secret-code" not in str(body)  # 密码不回显
    assert body["password_set"] is True

    # 密码为空保存 → 保留旧密码（password_set 仍 True）
    resp = await client.put(
        "/api/admin/settings/smtp",
        json={"host": "smtp2.example.com", "port": 465, "user": "noreply@example.com", "password": ""},
        headers=admin_headers,
    )
    assert resp.json()["host"] == "smtp2.example.com"
    assert resp.json()["password_set"] is True

    # 测试发信：未配置真实 SMTP → sent=False 但不报错
    resp = await client.post(
        "/api/admin/settings/smtp/test", json={"to": "someone@example.com"}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["sent"] is False


async def test_announcement_toggle(client, admin_headers):
    resp = await client.put(
        "/api/admin/settings/announcement",
        json={"enabled": True, "level": "warning", "text": "系统维护公告：今晚 22:00-23:00", "link": "/about"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    # 公开端点可见
    resp = await client.get("/api/site-config")
    body = resp.json()
    assert body["announcement"]["text"].startswith("系统维护公告")
    assert body["announcement"]["level"] == "warning"
    # 公开端点不含 SMTP
    assert "smtp" not in body

    # 空文本自动关闭
    resp = await client.put(
        "/api/admin/settings/announcement",
        json={"enabled": True, "text": "  "},
        headers=admin_headers,
    )
    assert resp.json()["enabled"] is False


async def test_texts_whitelist_and_defaults(client, admin_headers):
    # 白名单内的 key 可改
    resp = await client.put(
        "/api/admin/settings/texts",
        json={"footer_note": "自定义页脚 · AniSignal"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["footer_note"] == "自定义页脚 · AniSignal"

    # 未知 key 被拒
    resp = await client.put(
        "/api/admin/settings/texts", json={"evil_key": "x"}, headers=admin_headers
    )
    assert resp.status_code == 400

    # 公开端点合并默认值
    resp = await client.get("/api/site-config")
    texts = resp.json()["texts"]
    assert texts["footer_note"] == "自定义页脚 · AniSignal"
    assert "official_note" in texts  # 未改的用默认


async def test_ai_settings_masked_and_test(client, admin_headers):
    resp = await client.put(
        "/api/admin/settings/ai",
        json={"base_url": "https://api.example.com/v1", "model": "test-model", "api_key": "sk-secret"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key_set"] is True
    assert "sk-secret" not in str(body)

    # key 留空保留旧值
    resp = await client.put(
        "/api/admin/settings/ai", json={"base_url": "https://api.example.com/v1", "model": "m2"}, headers=admin_headers
    )
    assert resp.json()["api_key_set"] is True

    # 连通测试：配置的是假地址，应返回失败但不抛错
    resp = await client.post("/api/admin/settings/ai/test", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


async def test_ai_usage_recorded(client, session, admin_headers, monkeypatch):
    """AI 调用记账：usage 落库，费用按单价计算。"""
    from app.services import ai as ai_mod

    # 假 LLM 返回
    async def fake_chat(config, system, user, *, json_mode=False):
        return '{"summary": "测试摘要", "facts": [{"label": "测试", "value": "1"}]}', {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

    monkeypatch.setattr(ai_mod, "chat", fake_chat)
    # 直接写 ai 配置（含定价）
    from app.services.settings import set_setting

    await set_setting(session, "ai", {
        "base_url": "https://api.example.com/v1",
        "model": "m",
        "api_key": "sk-x",
        "price_in": 2.0,
        "price_out": 8.0,
    })

    # 用新端点（/api/admin/ai/summarize-all 依赖事件存在）
    from app.services.events import run_aggregation
    from app.services.ingest import ingest_items
    from tests.test_parsers_others import KURO_ITEM
    from sqlalchemy import select
    from app.models import AiUsage, Event

    await ingest_items(session, "kuro_notices", [KURO_ITEM])
    await run_aggregation(session)
    await session.commit()
    event_id = (await session.scalars(select(Event.id))).first()

    result = await ai_mod.summarize_event(session, event_id)
    assert result["status"] == "ok"

    usage = await session.scalar(select(AiUsage).order_by(AiUsage.id.desc()))
    assert usage is not None
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 150
    assert abs(usage.cost - (100 * 2.0 + 50 * 8.0) / 1e6) < 1e-9
    assert usage.task == "summarize_event"

    # 用量统计端点
    resp = await client.get("/api/admin/ai/usage", headers=admin_headers)
    body = resp.json()
    assert body["window"]["total_tokens"] >= 150
    assert any(t["task"] == "summarize_event" for t in body["by_task"])
    resp = await client.get("/api/admin/ai/calls", headers=admin_headers)
    assert resp.status_code == 200


async def test_ai_summary_uses_latest_plain_evidence_and_rejects_unsupported_game(
    session, monkeypatch
):
    """摘要只读最新证据；模型凭空换成其它游戏时使用可信回退文案。"""
    from sqlalchemy import select

    from app.models import ContentVersion, Event, RawContent
    from app.services import ai as ai_mod
    from app.services.events import run_aggregation
    from app.services.ingest import ingest_items
    from app.services.settings import set_setting
    from tests.test_parsers_others import KURO_ITEM

    await set_setting(
        session,
        "ai",
        {"base_url": "https://api.example.com/v1", "model": "m", "api_key": "sk-x"},
    )
    await ingest_items(session, "kuro_notices", [KURO_ITEM])
    raw = await session.scalar(select(RawContent))
    session.add(
        ContentVersion(
            raw_content_id=raw.id,
            version=2,
            title="3.6版本活动说明",
            text="<p>活动时间：3.6版本更新后 ~ 2026年9月29日03:59</p>",
            parser_version="test",
            category="version_update",
        )
    )
    await run_aggregation(session)
    await session.commit()
    event = await session.scalar(select(Event).where(Event.is_active.is_(True)))
    deterministic_facts = list(event.facts)
    captured = {}

    async def fake_chat(config, system, user, *, json_mode=False):
        captured["system"] = system
        captured["user"] = user
        return '{"summary": "战双帕弥什推出全新回馈活动", "facts": [{"label": "时间", "value": "未知"}]}', {}

    monkeypatch.setattr(ai_mod, "chat", fake_chat)
    result = await ai_mod.summarize_event(session, event.id)
    await session.refresh(event)

    assert result["status"] == "ok"
    assert "2026年9月29日03:59" in captured["user"]
    assert "<p>" not in captured["user"]
    assert "战双帕弥什" not in event.summary
    assert event.facts == deterministic_facts
    assert ai_mod._summary_supported("《鸣潮》活动《先约电台》现已开启", "[先约电台]活动说明")
