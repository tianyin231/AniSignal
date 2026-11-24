"""用户通用 Webhook 推送服务（Bark / 飞书 / 钉钉 / 企业微信 / Discord / ntfy / 通用 JSON）。

安全与配额边界：
- 写入侧校验公网 http(s) URL；每次实际出站前重新解析 DNS 并校验目标 IP 不属于回环/内网/云元数据段；
- 关闭 HTTP 重定向（follow_redirects=False），防止 302 跳转到内网地址；
- 与邮件/Bot 共用北京时间每日通知配额（claim_notification），并用 NotificationDelivery.dedupe_key 防重复投递。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import socket
import time
from email.header import Header
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.collectors.rss import _is_permitted_egress_ip
from app.communities import get_community
from app.config import get_settings
from app.models import ContentVersion, Event, EventRevision, Evidence, NotificationDelivery, RawContent, Source, User, UserWebhook
from app.services.account_limits import QUOTA_EXHAUSTED, claim_notification
from app.services.hidden_communities import account_blocks
from app.services.instant_notifications import _business_revision, _important, _matching_items
from app.services.notifications import _plain, _safe_url, beijing_today
from app.services.revision_notifications import load_official_revision_events, revision_webhook_payload
from app.services.urls import is_public_http_url

logger = logging.getLogger(__name__)

MAX_WEBHOOKS = 3
WEBHOOK_TIMEOUT_SECONDS = 10.0
WEBHOOK_PROVIDERS = ("generic", "bark", "feishu", "dingtalk", "wecom", "discord", "ntfy")
WEBHOOK_MODES = ("keywords", "all", "revisions")
_TEST_COOLDOWN_SECONDS = 10.0
_TEST_LAST_AT: dict[int, float] = {}


class WebhookError(ValueError):
    """Webhook 配置或出站校验失败。"""


def validate_webhook_url_static(url: str) -> str:
    cleaned = (url or "").strip()
    if not is_public_http_url(cleaned):
        raise WebhookError("Webhook 地址必须是公开的 http(s) 地址，不能指向本机或内网")
    parsed = urlsplit(cleaned)
    if parsed.username or parsed.password:
        raise WebhookError("Webhook 地址不能包含内嵌账号密码，请改用密钥字段")
    return cleaned


async def ensure_public_webhook_target(url: str) -> str:
    """出站前实时 DNS 解析检查，拦截 DNS 重绑定与私网/云元数据 IP。"""
    cleaned = validate_webhook_url_static(url)
    parsed = urlsplit(cleaned)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise WebhookError("Webhook 域名无法解析") from exc
    if not addresses:
        raise WebhookError("Webhook 域名无可用解析记录")
    allow_networks = get_settings().egress_allow_networks
    for row in addresses:
        addr = row[4][0]
        if not _is_permitted_egress_ip(addr, allow_networks):
            raise WebhookError(f"Webhook 目标解析到不可访问的私网地址（{addr}）")
    return cleaned


def _append_query(url: str, extra: dict[str, str]) -> str:
    parts = urlsplit(url)
    pairs = list(parse_qsl(parts.query, keep_blank_values=True))
    pairs.extend(extra.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _feishu_sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _dingtalk_sign(timestamp_ms: str, secret: str) -> str:
    string_to_sign = f"{timestamp_ms}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def build_webhook_request(
    provider: str,
    url: str,
    secret: str | None,
    payload: dict,
    *,
    now_ts: int | None = None,
) -> tuple[str, dict[str, str], bytes]:
    """按不同推送渠道构造 (target_url, headers, body_bytes)。"""
    ts = int(time.time()) if now_ts is None else int(now_ts)
    title = _plain(payload.get("title") or "AniSignal 通知")[:160]
    summary = _plain(payload.get("summary") or payload.get("note") or "")[:600]
    community_label = _plain(payload.get("community_label") or payload.get("community") or "")
    keywords = [str(k) for k in (payload.get("keywords") or []) if k]
    detail_url = _safe_url(payload.get("url")) or get_settings().public_base_url.rstrip("/")
    sources = payload.get("sources") or []

    lines: list[str] = []
    if community_label:
        lines.append(f"分区：{community_label}")
    if keywords:
        lines.append(f"命中词：{'、'.join(keywords)}")
    if summary:
        lines.append(summary)
    if sources:
        top = sources[0]
        src_title = _plain(top.get("title") or "")[:120]
        src_name = _plain(top.get("source") or "")
        if src_title:
            lines.append(f"来源：{src_title}" + (f"（{src_name}）" if src_name else ""))
    plain_body = "\n".join(lines) or title

    headers: dict[str, str] = {"User-Agent": "AniSignal-Webhook/1.0"}
    target_url = url

    if provider == "bark":
        body_obj = {
            "title": title,
            "body": plain_body[:1500],
            "url": detail_url,
            "group": "AniSignal",
        }
        headers["Content-Type"] = "application/json; charset=utf-8"
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        return target_url, headers, json.dumps(body_obj, ensure_ascii=False).encode("utf-8")

    if provider == "feishu":
        md_lines = [line for line in lines]
        md_lines.append(f"[查看详情]({detail_url})")
        body_obj: dict = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title[:100]},
                    "template": "orange" if payload.get("type") == "content_revision" else "blue",
                },
                "elements": [{"tag": "markdown", "content": "\n".join(md_lines)[:3000]}],
            },
        }
        if secret:
            ts_str = str(ts)
            body_obj["timestamp"] = ts_str
            body_obj["sign"] = _feishu_sign(ts_str, secret)
        headers["Content-Type"] = "application/json; charset=utf-8"
        return target_url, headers, json.dumps(body_obj, ensure_ascii=False).encode("utf-8")

    if provider == "dingtalk":
        md_text = f"### {title}\n\n" + "\n\n".join(lines) + f"\n\n[查看详情]({detail_url})"
        body_obj = {
            "msgtype": "markdown",
            "markdown": {"title": title[:100], "text": md_text[:3000]},
        }
        if secret:
            ts_ms = str(ts * 1000)
            target_url = _append_query(
                target_url, {"timestamp": ts_ms, "sign": _dingtalk_sign(ts_ms, secret)}
            )
        headers["Content-Type"] = "application/json; charset=utf-8"
        return target_url, headers, json.dumps(body_obj, ensure_ascii=False).encode("utf-8")

    if provider == "wecom":
        md_text = f"**{title}**\n" + "\n".join(lines) + f"\n[查看详情]({detail_url})"
        body_obj = {"msgtype": "markdown", "markdown": {"content": md_text[:3500]}}
        headers["Content-Type"] = "application/json; charset=utf-8"
        return target_url, headers, json.dumps(body_obj, ensure_ascii=False).encode("utf-8")

    if provider == "discord":
        fields = []
        if community_label:
            fields.append({"name": "分区", "value": community_label, "inline": True})
        if keywords:
            fields.append({"name": "命中关注词", "value": "、".join(keywords)[:256], "inline": True})
        embed: dict = {
            "title": title[:256],
            "url": detail_url,
            "description": (summary or plain_body)[:2000],
            "color": 15105570 if payload.get("type") == "content_revision" else 3447003,
            "footer": {"text": "AniSignal"},
        }
        if fields:
            embed["fields"] = fields
        body_obj = {"username": "AniSignal", "embeds": [embed]}
        headers["Content-Type"] = "application/json; charset=utf-8"
        return target_url, headers, json.dumps(body_obj, ensure_ascii=False).encode("utf-8")

    if provider == "ntfy":
        # HTTP 头必须是 ASCII/latin-1，非 ASCII 标题用 RFC 2047 编码，ntfy 服务端原生支持解码。
        headers["Title"] = Header(title[:160], "utf-8").encode(maxlinelen=998)
        headers["Click"] = detail_url
        headers["Tags"] = "bell"
        headers["Content-Type"] = "text/plain; charset=utf-8"
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        return target_url, headers, plain_body[:2000].encode("utf-8")

    # generic JSON
    body_obj = {
        "source": "anisignal",
        "type": payload.get("type") or "event_update",
        "timestamp": ts,
        "event": {
            "id": payload.get("event_id"),
            "title": title,
            "summary": summary,
            "community": payload.get("community"),
            "community_label": community_label,
            "category": payload.get("category"),
            "keywords": keywords,
            "url": detail_url,
            "sources": sources[:5],
        },
    }
    if payload.get("type") == "content_revision":
        body_obj["revision"] = {
            "content_id": payload.get("content_id"),
            "from_version": payload.get("from_version"),
            "to_version": payload.get("to_version"),
            "change_label": payload.get("change_label"),
            "change_lines": payload.get("change_lines") or [],
        }
    raw_bytes = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers["Content-Type"] = "application/json; charset=utf-8"
    if secret:
        ts_str = str(ts)
        sig = hmac.new(
            secret.encode("utf-8"),
            f"{ts_str}.".encode("utf-8") + raw_bytes,
            hashlib.sha256,
        ).hexdigest()
        headers["X-AniSignal-Timestamp"] = ts_str
        headers["X-AniSignal-Signature"] = f"sha256={sig}"
    return target_url, headers, raw_bytes


async def send_webhook_http(
    provider: str,
    url: str,
    secret: str | None,
    payload: dict,
) -> tuple[bool, int | None, str | None]:
    """执行一次 SSRF 受检的 Webhook POST；返回 (ok, status_code, error_message)。"""
    try:
        checked_url = await ensure_public_webhook_target(url)
    except WebhookError as exc:
        return False, None, str(exc)[:255]

    target_url, headers, body = build_webhook_request(provider, checked_url, secret, payload)
    try:
        async with httpx.AsyncClient(
            timeout=WEBHOOK_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            resp = await client.post(target_url, content=body, headers=headers)
        if 200 <= resp.status_code < 300:
            # 飞书/钉钉/企业微信部分错误返回 HTTP 200 + 非零 errcode/code，做轻量语义检查。
            if provider in {"feishu", "dingtalk", "wecom"}:
                try:
                    data = resp.json()
                    code = data.get("errcode", data.get("code", 0))
                    if isinstance(code, int) and code != 0:
                        msg = str(data.get("errmsg") or data.get("msg") or f"code={code}")
                        return False, resp.status_code, f"接口返回错误：{msg}"[:255]
                except ValueError:
                    pass
            return True, resp.status_code, None
        if 300 <= resp.status_code < 400:
            return False, resp.status_code, f"目标返回重定向 HTTP {resp.status_code}（已拦截）"
        return False, resp.status_code, f"HTTP {resp.status_code}"
    except httpx.TimeoutException:
        return False, None, "请求超时（10 秒）"
    except httpx.HTTPError as exc:
        return False, None, f"网络请求失败（{type(exc).__name__}）"[:255]


async def test_webhook(session: AsyncSession, webhook: UserWebhook) -> dict:
    """触发一次用户主动测试推送（单通道 10 秒冷却）。"""
    now_mono = time.monotonic()
    last = _TEST_LAST_AT.get(webhook.id)
    if last is not None and now_mono - last < _TEST_COOLDOWN_SECONDS:
        wait = max(1, int(_TEST_COOLDOWN_SECONDS - (now_mono - last)))
        raise WebhookError(f"测试过于频繁，请 {wait} 秒后再试")
    _TEST_LAST_AT[webhook.id] = now_mono

    base = get_settings().public_base_url.rstrip("/")
    payload = {
        "type": "test",
        "event_id": None,
        "title": f"AniSignal Webhook 测试 · {webhook.name}",
        "summary": "如果你看到这条消息，说明当前 Webhook 通道已连通，后续命中关注条件的重要事件会自动送达。",
        "community": "all",
        "community_label": "测试推送",
        "category": "test",
        "keywords": ["连接测试"],
        "url": f"{base}/account?tab=notify",
        "sources": [],
    }
    ok, status_code, error = await send_webhook_http(
        webhook.provider, webhook.url, webhook.secret, payload
    )
    webhook.last_status = "ok" if ok else "failed"
    webhook.last_error = error
    webhook.last_sent_at = utcnow()
    await session.commit()
    return {"ok": ok, "status_code": status_code, "error": error}


def _event_matches_preferences(user: User, event: Event) -> bool:
    prefs = user.feed_preferences or {}
    communities = prefs.get("communities") or []
    if communities:
        if event.community not in communities:
            return False
    else:
        blocks = account_blocks(user)
        if event.community in blocks.communities:
            return False
    title_lower = (event.title or "").lower()
    for excluded in prefs.get("exclude_keywords") or []:
        if excluded and str(excluded).lower() in title_lower:
            return False
    return True


async def _event_sources(session: AsyncSession, event_id: int) -> list[dict]:
    rows = await session.execute(
        select(RawContent, ContentVersion, Source.name)
        .join(Evidence, Evidence.raw_content_id == RawContent.id)
        .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
        .outerjoin(Source, Source.id == RawContent.source_id)
        .where(
            Evidence.event_id == event_id,
            Evidence.role != "excluded",
            RawContent.hidden.is_(False),
        )
        .order_by(RawContent.published_at.desc(), RawContent.id.desc())
        .limit(3)
    )
    return [
        {
            "id": raw.id,
            "title": version.title or raw.external_id,
            "source": source_name or raw.platform,
            "url": _safe_url(raw.url),
        }
        for raw, version, source_name in rows
    ]


def _event_payload(
    event: Event,
    revision: EventRevision,
    *,
    keywords: list[str],
    sources: list[dict],
) -> dict:
    meta = get_community(event.community)
    base = get_settings().public_base_url.rstrip("/")
    return {
        "type": "event_update",
        "event_id": event.id,
        "title": event.title,
        "summary": event.summary or revision.note,
        "note": revision.note,
        "community": event.community,
        "community_label": meta.label,
        "category": event.category,
        "keywords": keywords,
        "url": f"{base}/events/{event.id}",
        "sources": sources,
    }


async def run_webhook_notifications(session: AsyncSession) -> dict:
    """扫描已启用 Webhook 的活跃验证用户，按游标推送新发生的重要事件变化。"""
    user_ids = list(
        await session.scalars(
            select(User.id)
            .join(UserWebhook, UserWebhook.user_id == User.id)
            .where(
                User.status == "active",
                User.is_verified.is_(True),
                UserWebhook.enabled.is_(True),
            )
            .distinct()
        )
    )
    stats = {"sent": 0, "failed": 0, "skipped": 0}
    for user_id in user_ids:
        user = await session.get(User, user_id, populate_existing=True, with_for_update=True)
        if not user or user.status != "active" or not user.is_verified:
            await session.commit()
            continue
        webhooks = list(
            await session.scalars(
                select(UserWebhook)
                .where(UserWebhook.user_id == user.id, UserWebhook.enabled.is_(True))
                .order_by(UserWebhook.id)
            )
        )
        if not webhooks:
            await session.commit()
            continue

        event_webhooks = [wh for wh in webhooks if wh.events_mode in {"keywords", "all"}]
        revision_webhooks = [wh for wh in webhooks if wh.events_mode == "revisions" or wh.include_revisions]

        # 先准备要发送的批次并推进游标，避免外部 HTTP 超时拖住 User 行锁。
        pending_sends: list[tuple[int, int, str, str, str | None, dict]] = []

        if event_webhooks:
            if user.webhook_notification_cursor is None:
                user.webhook_notification_cursor = (
                    await session.scalar(
                        select(EventRevision.id).order_by(EventRevision.id.desc()).limit(1)
                    )
                    or 0
                )
            else:
                revisions = list(
                    await session.scalars(
                        select(EventRevision)
                        .where(EventRevision.id > user.webhook_notification_cursor)
                        .order_by(EventRevision.id)
                        .limit(200)
                    )
                )
                if revisions:
                    by_event = {row.event_id: row for row in revisions if _business_revision(row)}
                    for event_id, revision in by_event.items():
                        event = await session.get(Event, event_id)
                        if not event or not _important(event):
                            continue
                        kw_items: list[dict] | None = None
                        all_sources: list[dict] | None = None

                        for wh in event_webhooks:
                            if wh.events_mode == "keywords":
                                if kw_items is None:
                                    kw_items = await _matching_items(session, user, event)
                                if not kw_items:
                                    continue
                                matched_kws = sorted({k for item in kw_items for k in item["keywords"]})
                                sources = [
                                    {
                                        "id": item["id"],
                                        "title": item["title"],
                                        "source": item.get("source") or item["platform"],
                                        "url": _safe_url(item.get("url")),
                                    }
                                    for item in kw_items[:3]
                                ]
                                content_ids = [item["id"] for item in kw_items]
                            else:
                                if not _event_matches_preferences(user, event):
                                    continue
                                if all_sources is None:
                                    all_sources = await _event_sources(session, event.id)
                                matched_kws = []
                                sources = all_sources
                                content_ids = [s["id"] for s in sources]

                            dedupe_key = f"webhook:{wh.id}:{event.id}:{revision.id}"
                            if await session.scalar(
                                select(NotificationDelivery.id).where(
                                    NotificationDelivery.dedupe_key == dedupe_key
                                )
                            ):
                                continue

                            allowed = await claim_notification(session, user.id)
                            payload = _event_payload(event, revision, keywords=matched_kws, sources=sources)
                            delivery = NotificationDelivery(
                                user_id=user.id,
                                kind="webhook",
                                source="schedule",
                                period_date=beijing_today(),
                                dedupe_key=dedupe_key,
                                status="sending" if allowed else "skipped",
                                subject=f"{wh.name} · {event.title}"[:255],
                                body=payload["summary"][:2000],
                                matched_keywords=matched_kws,
                                content_ids=content_ids,
                                error=None if allowed else QUOTA_EXHAUSTED,
                            )
                            session.add(delivery)
                            await session.flush()
                            if allowed:
                                pending_sends.append(
                                    (delivery.id, wh.id, wh.provider, wh.url, wh.secret, payload)
                                )
                            else:
                                stats["skipped"] += 1

                    user.webhook_notification_cursor = revisions[-1].id

        if revision_webhooks:
            if user.webhook_revision_cursor is None:
                user.webhook_revision_cursor = (
                    await session.scalar(
                        select(ContentVersion.id).order_by(ContentVersion.id.desc()).limit(1)
                    )
                    or 0
                )
            else:
                prefs = user.feed_preferences or {}
                blocks = account_blocks(user)
                rev_events, max_ver_id = await load_official_revision_events(
                    session,
                    after_version_id=user.webhook_revision_cursor,
                    communities=prefs.get("communities") or None,
                    exclude_keywords=prefs.get("exclude_keywords") or None,
                    limit=100,
                )
                for item in rev_events:
                    if not prefs.get("communities") and item["community"] in blocks.communities:
                        continue
                    payload = revision_webhook_payload(item)
                    for wh in revision_webhooks:
                        dedupe_key = f"webhook-rev:{wh.id}:{item['content_id']}:{item['version_id']}"
                        if await session.scalar(
                            select(NotificationDelivery.id).where(
                                NotificationDelivery.dedupe_key == dedupe_key
                            )
                        ):
                            continue
                        allowed = await claim_notification(session, user.id)
                        delivery = NotificationDelivery(
                            user_id=user.id,
                            kind="webhook",
                            source="schedule",
                            period_date=beijing_today(),
                            dedupe_key=dedupe_key,
                            status="sending" if allowed else "skipped",
                            subject=f"{wh.name} · 官方修订 · {item['title']}"[:255],
                            body=payload["summary"][:2000],
                            matched_keywords=[],
                            content_ids=[item["content_id"]],
                            error=None if allowed else QUOTA_EXHAUSTED,
                        )
                        session.add(delivery)
                        await session.flush()
                        if allowed:
                            pending_sends.append(
                                (delivery.id, wh.id, wh.provider, wh.url, wh.secret, payload)
                            )
                        else:
                            stats["skipped"] += 1
                if max_ver_id > user.webhook_revision_cursor:
                    user.webhook_revision_cursor = max_ver_id

        await session.commit()

        # 行锁释放后再发外部 HTTP 请求，逐条回写投递状态。
        for delivery_id, wh_id, provider, url, secret, payload in pending_sends:
            ok, _status_code, error = await send_webhook_http(provider, url, secret, payload)
            delivery = await session.get(NotificationDelivery, delivery_id, populate_existing=True)
            wh = await session.get(UserWebhook, wh_id, populate_existing=True)
            now_dt = utcnow()
            if delivery is not None:
                delivery.status = "sent" if ok else "failed"
                delivery.error = error
                delivery.sent_at = now_dt if ok else None
            if wh is not None:
                wh.last_status = "ok" if ok else "failed"
                wh.last_error = error
                wh.last_sent_at = now_dt
            await session.commit()
            stats["sent" if ok else "failed"] += 1

    return stats
