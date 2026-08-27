import httpx

from app.services import mailer, notifier


async def test_mailer_uses_implicit_tls_for_port_465(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("aiosmtplib.send", fake_send)
    sent = await mailer.send_email(
        "to@example.com",
        "测试",
        "正文",
        smtp={"host": "smtp.example.com", "port": 465, "tls_mode": "tls"},
    )

    assert sent is True
    assert captured["use_tls"] is True
    assert captured["start_tls"] is False


async def test_mailer_uses_starttls_for_port_587(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("aiosmtplib.send", fake_send)
    sent = await mailer.send_email(
        "to@example.com",
        "测试",
        "正文",
        smtp={"host": "smtp.example.com", "port": 587, "tls_mode": "starttls"},
    )

    assert sent is True
    assert captured["use_tls"] is False
    assert captured["start_tls"] is True


async def test_onepush_reports_http_result(monkeypatch):
    monkeypatch.setattr(
        notifier,
        "_send_onepush_sync",
        lambda provider, params: httpx.Response(200),
    )

    result = await notifier.send_onepush(
        "标题",
        "正文",
        {"enabled": True, "config": {"provider": "pushplus", "token": "x"}},
    )

    assert result["ok"] is True
    assert result["status"] == "sent"


async def test_onepush_skips_when_disabled(monkeypatch):
    called = False

    def fake_send(provider, params):
        nonlocal called
        called = True

    monkeypatch.setattr(notifier, "_send_onepush_sync", fake_send)
    result = await notifier.send_onepush(
        "标题",
        "正文",
        {"enabled": False, "config": {"provider": "pushplus", "token": "x"}},
    )

    assert result["status"] == "disabled"
    assert called is False
