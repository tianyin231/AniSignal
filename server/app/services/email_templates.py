"""AniSignal 邮件模板：兼容邮件客户端的内联样式与纯文本降级。"""

from html import escape


def document(
    title: str,
    content: str,
    *,
    eyebrow: str = "ANISIGNAL",
    preheader: str = "",
) -> str:
    safe_title = escape(title)
    safe_eyebrow = escape(eyebrow)
    safe_preheader = escape(preheader or title)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>{safe_title}</title>
  <style>
    @media only screen and (max-width: 680px) {{
      .email-shell {{ width: 100% !important; }}
      .email-pad {{ padding-left: 20px !important; padding-right: 20px !important; }}
      .email-title {{ font-size: 24px !important; line-height: 32px !important; }}
      .email-button {{ display: block !important; text-align: center !important; }}
    }}
    @media (prefers-color-scheme: dark) {{
      .email-bg {{ background: #09090b !important; }}
      .email-card {{ background: #18181b !important; border-color: #27272a !important; }}
      .email-text {{ color: #f4f4f5 !important; }}
      .email-muted {{ color: #a1a1aa !important; }}
      .email-panel {{ background: #27272a !important; border-color: #3f3f46 !important; }}
    }}
  </style>
</head>
<body class="email-bg" style="margin:0;padding:0;background:#f1f5f9;color:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{safe_preheader}</div>
  <table role="presentation" class="email-bg" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:#f1f5f9;">
    <tr>
      <td align="center" style="padding:28px 12px;">
        <table role="presentation" class="email-shell email-card" width="640" cellspacing="0" cellpadding="0" border="0" style="width:640px;max-width:640px;background:#ffffff;border:1px solid #e2e8f0;border-radius:18px;overflow:hidden;">
          <tr>
            <td class="email-pad" style="padding:26px 32px;background-color:#1d4ed8;background-image:linear-gradient(135deg,#2563eb,#0f4bb8);">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td width="44" valign="middle">
                    <div style="width:36px;height:36px;line-height:36px;text-align:center;border-radius:10px;background:#ffffff;color:#1d4ed8;font-size:18px;font-weight:800;">A</div>
                  </td>
                  <td valign="middle" style="padding-left:10px;color:#dbeafe;font-size:12px;line-height:18px;font-weight:700;letter-spacing:1.5px;">{safe_eyebrow}</td>
                </tr>
              </table>
              <h1 class="email-title" style="margin:18px 0 0;color:#ffffff;font-size:28px;line-height:36px;font-weight:750;">{safe_title}</h1>
            </td>
          </tr>
          <tr>
            <td class="email-pad email-text" style="padding:30px 32px;color:#0f172a;font-size:15px;line-height:1.75;">
              {content}
            </td>
          </tr>
          <tr>
            <td class="email-pad email-muted" style="padding:20px 32px;border-top:1px solid #e2e8f0;color:#64748b;font-size:12px;line-height:20px;">
              AniSignal · 鸣潮跨平台事件雷达<br>
              这是一封系统服务邮件，请勿直接回复。
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def paragraphs(text: str) -> str:
    parts = [line.strip() for line in text.splitlines() if line.strip()]
    return "".join(
        f'<p class="email-text" style="margin:0 0 14px;color:#334155;">{escape(line)}</p>'
        for line in parts
    )


def notice_email(title: str, text: str, *, preheader: str = "") -> str:
    panel = (
        '<div class="email-panel" style="padding:18px 20px;background:#f8fafc;'
        'border:1px solid #e2e8f0;border-radius:12px;">'
        f"{paragraphs(text)}</div>"
    )
    return document(title, panel, preheader=preheader or text[:90])


def code_email(action: str, code: str, minutes: int) -> str:
    content = f"""
      <p class="email-text" style="margin:0 0 18px;color:#334155;">你正在进行 AniSignal {escape(action)}，验证码如下：</p>
      <div class="email-panel" style="margin:0 0 18px;padding:20px;text-align:center;background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;">
        <div style="color:#1d4ed8;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:32px;line-height:40px;font-weight:800;letter-spacing:8px;">{escape(code)}</div>
      </div>
      <p class="email-muted" style="margin:0;color:#64748b;font-size:13px;">验证码 {minutes} 分钟内有效。如果这不是你本人的操作，请忽略本邮件。</p>
    """
    return document(
        f"{action}验证码",
        content,
        eyebrow="ANISIGNAL ACCOUNT",
        preheader=f"你的 AniSignal {action}验证码是 {code}",
    )
