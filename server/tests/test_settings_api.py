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
