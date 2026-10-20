import httpx

from app.services import mailer, notifier


async def test_mailer_uses_implicit_tls_for_port_465(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message
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


async def test_mailer_sends_plain_and_html_alternatives(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message

    monkeypatch.setattr("aiosmtplib.send", fake_send)
    sent = await mailer.send_email(
        "to@example.com",
        "测试",
        "纯文本正文",
        smtp={"host": "smtp.example.com", "port": 465, "tls_mode": "tls"},
        html_body="<html><body><strong>HTML 正文</strong></body></html>",
    )

    assert sent is True
    message = captured["message"]
    assert message.get_content_type() == "multipart/alternative"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "纯文本正文"
    assert "<strong>HTML 正文</strong>" in message.get_body(preferencelist=("html",)).get_content()


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


# ---------- 邮件头折行（含换行的主题不再打断发送） ----------


async def test_mailer_folds_crlf_in_subject_and_to(monkeypatch):
    """主题/收件人里的换行会让 EmailMessage 抛 ValueError，折叠后必须能正常发信。"""
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message

    monkeypatch.setattr("aiosmtplib.send", fake_send)
    sent = await mailer.send_email(
        "to@example.com\r\nBcc: victim@example.com",
        "标题\r\nBcc: victim@example.com",
        "正文",
        smtp={"host": "smtp.example.com", "port": 465, "tls_mode": "tls"},
    )

    assert sent is True
    message = captured["message"]
    assert "\n" not in str(message["Subject"])
    assert "Bcc" not in message.keys()
    assert "victim@example.com" in str(message["Subject"])  # 折叠成正文，不是注入头


async def test_mailer_folds_other_control_chars_and_unicode_separators(monkeypatch):
    """只清 \\r\\n 是不够的：VT/FF/U+2028/U+2029/U+0085 同样会被 email 库拒收。"""
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message

    monkeypatch.setattr("aiosmtplib.send", fake_send)
    for raw in ("标题\x0bBcc: x@example.com", "标题\x0cBAD", "标题\u2028BAD", "标题\u2029BAD", "标题\u0085BAD"):
        sent = await mailer.send_email(
            "to@example.com",
            raw,
            "正文",
            smtp={"host": "smtp.example.com", "port": 465, "tls_mode": "tls"},
        )
        assert sent is True, raw
        assert "\n" not in str(captured["message"]["Subject"])


def test_header_text_strips_control_chars_that_split_misses():
    """str.split() 能吃掉的只是空白类字符；\x00/\x7f/\x9f 这类必须靠正则剥。"""
    assert mailer._header_text("a\x00b") == "a b"
    assert mailer._header_text("a\x7fb") == "a b"
    assert mailer._header_text("a\x9fb") == "a b"
    assert mailer._header_text("a\x1fb") == "a b"


def test_header_text_collapses_and_truncates():
    assert mailer._header_text("a\r\nb\tc") == "a b c"
    assert mailer._header_text(None) == ""
    assert len(mailer._header_text("x" * 400)) == 255
