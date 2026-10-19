"""通用 Webhook 通知渠道：命中 → JSON POST（n8n 兼容）→ 失败重试 → 投递记录。

触发链路与邮件/机器人推送解耦：按用户启用中的关键词扫最近命中（与通知服务同一套
候选口径），为每个启用中的 webhook POST 一次 JSON，同一内容按 dedupe_key 去重，
失败重试并留记录。payload 字段固定为 title/link/content/source/timestamp/tier，
n8n 的 Webhook 节点可直接消费；额外带 id/community/keywords 便于自动化分流。

安全边界：只接受 http/https，拒绝 localhost 与内网/保留 IP 字面量（用户可配 URL，
不能把自托管实例当成内网扫描器）。字面量检查挡不住 `127.0.0.1.nip.io` 这类域名，
也挡不住"创建时是公网、事后把 DNS 改指向内网"，因此投递前会重新解析并逐个 IP 复核。
"""

import asyncio
import ipaddress
import logging
import socket
import time
from datetime import timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import (
    WEBHOOK_TIER_ALL,
    RawContent,
    User,
    WebhookConfig,
    WebhookDelivery,
)

logger = logging.getLogger(__name__)

# 单次请求超时（秒）：外部服务慢时不能拖住调度
WEBHOOK_TIMEOUT = 10.0
# 失败重试次数（含首次，共 3 次 POST），与机器人投递的 attempts 上限一致
MAX_ATTEMPTS = 3
RETRY_BACKOFF = (0.5, 1.0)

# 命中回看窗口与单轮上限：只推最近命中，避免一轮把历史全推一遍
HIT_LOOKBACK_HOURS = 24
MAX_HITS_PER_RUN = 10

USER_AGENT = "AniSignal-Webhook/1.0"
CONTENT_MAX = 500

# DNS 复核结果的缓存时长：必须远小于"改一次 DNS 记录"的成本，缓存的是判定结果
# 而不是 IP，TTL 内重复投递不再解析（同一 host 每轮最多几条，成本可忽略）
DNS_CHECK_TTL = 60.0
_dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}

_BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    # 裸域名形式的「解析到内网」公共后缀（SEC-021）：不解析也要先拒一层
    "nip.io",
    "sslip.io",
    "localtest.me",
}
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".nip.io",
    ".sslip.io",
    ".localtest.me",
    ".vcap.me",
)


def _is_internal_address(address: str) -> bool:
    """单个 IP 是否落在不可投递区间（回环/私网/链路本地/保留/组播/未指定）。"""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    # ::ffff:127.0.0.1 这类 IPv4-mapped 的 is_loopback 是 False，必须先解包
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _resolved_addresses(host: str) -> tuple[str, ...]:
    """解析 host 的全部 A/AAAA；解析失败返回空 tuple（POST 自然会失败）。"""
    now = time.monotonic()
    cached = _dns_cache.get(host)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return ()
    addresses = tuple(sorted({info[4][0] for info in infos}))
    _dns_cache[host] = (now + DNS_CHECK_TTL, addresses)
    return addresses


def is_safe_webhook_url(url: str) -> bool:
    """URL 形态与目标校验：http/https，且不指向本机或内网地址。"""
    candidate = (url or "").strip()
    if not candidate or len(candidate) > 512:
        return False
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # 域名在此处不解析（阻塞 DNS）；投递前的 _delivery_target_safe 会判
    return not _is_internal_address(host)


async def _delivery_target_safe(url: str) -> bool:
    """投递前的 SSRF 收敛：形态校验 + 解析后逐个 IP 复核。

    getaddrinfo 会把十进制/十六进制/短形式（`2130706433`、`127.1`、`0x7f000001`）
    与 `*.nip.io` 这类域名一并归一成真实 IP，所以这一道能盖住字面量检查的全部绕过写法。
    """
    if not is_safe_webhook_url(url):
        return False
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    addresses = await _resolved_addresses(host)
    if not addresses:
        return True  # 解析不了：请求本身就会失败，不额外放行也不误杀
    return all(not _is_internal_address(address) for address in addresses)


def _clean(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def build_payload(item: dict) -> dict:
    """n8n 兼容 payload：title/link/content/source/timestamp/tier + 分流辅助字段。"""
    published = item.get("published_at") or utcnow()
    return {
        "title": _clean(item.get("title"), 255),
        "link": item.get("url") or "",
        "content": _clean(item.get("summary") or item.get("text"), CONTENT_MAX),
        "source": _clean(item.get("source") or item.get("platform"), 120),
        "timestamp": published.replace(tzinfo=timezone.utc).isoformat(),
        "tier": item.get("tier") or "normal",
        "id": item.get("id"),
        "community": item.get("community"),
        "keywords": list(item.get("keywords") or []),
    }


def tier_allowed(webhook: WebhookConfig, item: dict) -> bool:
    """推送范围：important 只放重要命中；all 放行所有命中。"""
    if (webhook.tier_filter or "") == WEBHOOK_TIER_ALL:
        return True
    return (item.get("tier") or "normal") == "important"


async def _post(url: str, payload: dict) -> tuple[int | None, str | None]:
    """POST 一次；返回 (状态码, 错误信息)。错误信息为 None 表示 2xx 成功。"""
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=False) as client:
        response = await client.post(
            url,
            json=payload,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
    code = int(response.status_code)
    if 200 <= code < 300:
        return code, None
    return code, f"HTTP {code}: {_clean(response.text, 200)}"


async def deliver_webhook(
    session: AsyncSession,
    webhook: WebhookConfig,
    payload: dict,
    *,
    kind: str = "hit",
    content_id: int | None = None,
    dedupe_key: str | None = None,
    row: WebhookDelivery | None = None,
) -> WebhookDelivery:
    """带重试的投递：最多 MAX_ATTEMPTS 次 POST，结果与错误全部落库。"""
    # 投递前重新收敛目标地址：创建时校验挡不住 DNS 事后改指向（rebinding）
    target_unsafe = not await _delivery_target_safe(webhook.url)
    if row is None:
        row = WebhookDelivery(
            webhook_id=webhook.id,
            user_id=webhook.user_id,
            kind=kind,
            content_id=content_id,
            title=str(payload.get("title") or "")[:255],
            payload=payload,
            dedupe_key=dedupe_key,
            status="pending",
        )
        session.add(row)
        try:
            await session.commit()
        except Exception:  # noqa: BLE001 - 去重键撞车：复用已有记录，不重复打扰
            await session.rollback()
            existing = await session.scalar(
                select(WebhookDelivery).where(
                    WebhookDelivery.dedupe_key == dedupe_key
                )
            )
            if existing is None:
                raise
            row = existing
    for attempt in range(1, MAX_ATTEMPTS + 1):
        code: int | None = None
        error: str | None = None
        if target_unsafe:
            error = "目标地址未通过安全校验（解析到内网/回环/保留地址），已拒绝投递"
        else:
            try:
                code, error = await _post(webhook.url, payload)
            except Exception as exc:  # noqa: BLE001 - 网络错误按失败重试
                error = f"{type(exc).__name__}: {exc}"[:300]
        row.attempts = attempt
        row.response_code = code
        row.error = error
        row.updated_at = utcnow()
        if error is None:
            row.status = "sent"
            row.sent_at = utcnow()
            await session.commit()
            logger.info(
                "webhook %s delivered content=%s attempt=%s", webhook.id, content_id, attempt
            )
            return row
        logger.warning(
            "webhook %s attempt %s failed for content=%s: %s",
            webhook.id, attempt, content_id, error,
        )
        if target_unsafe:
            break  # 目标地址非法不是瞬时故障，重试不会变好
    row.status = "failed"
    await session.commit()
    return row


async def matching_hits(
    session: AsyncSession,
    keywords: list[str],
    *,
    limit: int = MAX_HITS_PER_RUN,
) -> list[dict]:
    """用户关键词的最近强命中：标题/溯源命中，且过通知降噪与意图忽略两道闸。"""
    if not keywords:
        return []
    from app.services.notifications import _is_low_signal_notification, load_candidates

    end_at = utcnow()
    start_at = end_at - timedelta(hours=HIT_LOOKBACK_HOURS)
    candidates = await load_candidates(session, keywords, start_at, end_at)
    strong = [
        item
        for item in candidates
        if item.get("strength") == "strong"
        and not _is_low_signal_notification(item)
    ]
    if not strong:
        return []
    tiers = dict(
        (
            await session.execute(
                select(RawContent.id, RawContent.intent_tier).where(
                    RawContent.id.in_([item["id"] for item in strong])
                )
            )
        ).all()
    )
    hits = []
    for item in strong:
        tier = tiers.get(item["id"])
        if tier == "ignore":
            continue  # 意图打标忽略的内容不推送（与机器人推送同一口径）
        item["tier"] = tier or "normal"
        hits.append(item)
    return hits[:limit]


async def dispatch_user(
    session: AsyncSession, user_id: int, webhooks: list[WebhookConfig]
) -> dict:
    """给一个用户的所有启用 webhook 推送本轮命中（按内容去重）。"""
    from app.services.notifications import enabled_keywords

    stats = {"sent": 0, "failed": 0, "skipped": 0}
    keywords = await enabled_keywords(session, user_id)
    if not keywords:
        return stats
    hits = await matching_hits(session, keywords)
    if not hits:
        return stats
    for webhook in webhooks:
        for item in hits:
            if not tier_allowed(webhook, item):
                stats["skipped"] += 1
                continue
            dedupe_key = f"webhook:{webhook.id}:content:{item['id']}"
            existing = await session.scalar(
                select(WebhookDelivery).where(
                    WebhookDelivery.dedupe_key == dedupe_key
                )
            )
            if existing is not None and (
                existing.status == "sent" or existing.attempts >= MAX_ATTEMPTS
            ):
                stats["skipped"] += 1
                continue
            row = await deliver_webhook(
                session,
                webhook,
                build_payload(item),
                content_id=item["id"],
                dedupe_key=dedupe_key,
                row=existing,
            )
            stats["sent" if row.status == "sent" else "failed"] += 1
    return stats


async def run_webhook_dispatch(session: AsyncSession) -> dict:
    """扫所有启用中的 webhook 并推送命中；没有配置时立刻返回（零成本）。"""
    hooks = list(
        (
            await session.execute(
                select(WebhookConfig)
                .where(WebhookConfig.enabled.is_(True))
                .order_by(WebhookConfig.id)
            )
        ).scalars()
    )
    stats = {"users": 0, "sent": 0, "failed": 0, "skipped": 0}
    if not hooks:
        return stats
    active_ids = set(
        await session.scalars(select(User.id).where(User.status == "active"))
    )
    by_user: dict[int, list[WebhookConfig]] = {}
    for hook in hooks:
        if hook.user_id in active_ids:
            by_user.setdefault(hook.user_id, []).append(hook)
    for user_id, user_hooks in by_user.items():
        stats["users"] += 1
        result = await dispatch_user(session, user_id, user_hooks)
        for key in ("sent", "failed", "skipped"):
            stats[key] += result[key]
    if stats["sent"] or stats["failed"]:
        logger.info("webhook dispatch -> %s", stats)
    return stats


async def send_test(session: AsyncSession, webhook: WebhookConfig) -> WebhookDelivery:
    """手动测试投递：固定示例 payload，让用户先确认链路可用。"""
    sample = {
        "title": "AniSignal Webhook 测试",
        "link": "",
        "content": "这是一条测试消息，用于确认 webhook 地址可用。",
        "source": "anisignal",
        "tier": "normal",
        "keywords": [],
    }
    return await deliver_webhook(
        session,
        webhook,
        build_payload(sample),
        kind="test",
    )
