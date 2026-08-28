"""关键词邮件：候选筛选、AI 精炼、投递记录与每日批处理。"""

import json
import logging
import re
import time
from datetime import date, datetime, time as day_time, timedelta, timezone
from html import unescape

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import (
    ContentTranslation,
    ContentVersion,
    Event,
    Evidence,
    NotificationDelivery,
    RawContent,
    Source,
    User,
    UserKeyword,
)
from app.services.classify import HIDDEN_CATEGORIES
from app.services.mailer import send_email

logger = logging.getLogger(__name__)

SHANGHAI = timezone(timedelta(hours=8))
MAX_ITEMS = 5


def beijing_today(now: datetime | None = None) -> date:
    value = now or utcnow()
    return value.replace(tzinfo=timezone.utc).astimezone(SHANGHAI).date()


def morning_window(day: date) -> tuple[datetime, datetime]:
    """当天早报覆盖前一日 07:50 至当天 07:50（北京时间）。"""
    end_local = datetime.combine(day, day_time(7, 50), tzinfo=SHANGHAI)
    start_local = end_local - timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _plain(text: str) -> str:
    import bleach

    value = unescape(bleach.clean(text or "", tags=[], strip=True))
    return re.sub(r"\s+", " ", value).strip()


async def enabled_keywords(session: AsyncSession, user_id: int) -> list[str]:
    return [
        row
        for row in await session.scalars(
            select(UserKeyword.keyword)
            .where(UserKeyword.user_id == user_id, UserKeyword.enabled.is_(True))
            .order_by(UserKeyword.id)
        )
    ]


async def load_candidates(
    session: AsyncSession,
    keywords: list[str],
    start_at: datetime,
    end_at: datetime,
) -> list[dict]:
    """只从明确出现用户关键词的内容中取候选；正文命中标为 weak，交给 AI 决定。"""
    if not keywords:
        return []
    latest_sq = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    published = func.coalesce(RawContent.published_at, RawContent.collected_at)
    rows = await session.execute(
        select(RawContent, ContentVersion, Source, ContentTranslation)
        .join(latest_sq, latest_sq.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest_sq.c.rid)
            & (ContentVersion.version == latest_sq.c.version),
        )
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .join(ContentTranslation, ContentTranslation.raw_content_id == RawContent.id, isouter=True)
        .where(
            published >= start_at,
            published < end_at,
            or_(
                ContentVersion.category.notin_(HIDDEN_CATEGORIES),
                ContentVersion.category.is_(None),
            ),
        )
        .order_by(published.desc(), RawContent.id.desc())
        .limit(200)
    )

    candidates: list[dict] = []
    for raw, version, source, translation in rows:
        title = _plain(version.title or raw.external_id)
        text = _plain(version.text or "")
        translated = _plain(translation.text if translation else "")
        title_folded = title.casefold()
        body_folded = f"{text}\n{translated}".casefold()
        provenance = [str(item).casefold() for item in (raw.matched_keywords or [])]
        strong: list[str] = []
        weak: list[str] = []
        for keyword in keywords:
            folded = keyword.casefold()
            if folded in title_folded or folded in provenance:
                strong.append(keyword)
            elif folded in body_folded:
                weak.append(keyword)
        if not strong and not weak:
            continue
        candidates.append(
            {
                "id": raw.id,
                "title": title,
                "text": (translated or text)[:800],
                "url": raw.url,
                "platform": raw.platform,
                "source": source.name if source else None,
                "identity": source.identity if source else "unknown",
                "published_at": raw.published_at or raw.collected_at,
                "keywords": strong or weak,
                "strength": "strong" if strong else "weak",
            }
        )
    return candidates


async def _event_map(session: AsyncSession, content_ids: list[int]) -> dict[int, Event]:
    if not content_ids:
        return {}
    rows = await session.execute(
        select(Evidence.raw_content_id, Event)
        .join(Event, Event.id == Evidence.event_id)
        .where(
            Evidence.raw_content_id.in_(content_ids),
            Evidence.role != "excluded",
            Event.is_active.is_(True),
        )
        .order_by(Event.importance.desc(), Event.last_updated_at.desc())
    )
    result: dict[int, Event] = {}
    for content_id, event in rows:
        result.setdefault(content_id, event)
    return result


async def group_candidates(session: AsyncSession, candidates: list[dict]) -> list[dict]:
    """同一事件的跨平台内容只保留一条，关键词和证据 ID 合并。"""
    events = await _event_map(session, [item["id"] for item in candidates])
    grouped: dict[str, dict] = {}
    for item in candidates:
        event = events.get(item["id"])
        key = f"event:{event.id}" if event else f"content:{item['id']}"
        existing = grouped.get(key)
        if existing:
            existing["content_ids"].append(item["id"])
            existing["keywords"] = sorted({*existing["keywords"], *item["keywords"]})
            if item.get("ai_summary") and not existing.get("ai_summary"):
                existing["ai_summary"] = item["ai_summary"]
            continue
        grouped[key] = {
            **item,
            "title": event.title if event else item["title"],
            "summary": (event.summary or event.conclusion) if event else item["text"],
            "event_id": event.id if event else None,
            "importance": bool(event.importance) if event else False,
            "content_ids": [item["id"]],
        }
    return sorted(
        grouped.values(),
        key=lambda item: (
            item["importance"],
            item["identity"] == "official",
            item["published_at"],
        ),
        reverse=True,
    )


async def ai_curate(
    session: AsyncSession,
    user_id: int,
    keywords: list[str],
    candidates: list[dict],
) -> tuple[list[dict] | None, str | None]:
    """让 AI 只从既有候选中筛选并精炼；返回 None 表示 AI 不可用或失败。"""
    from app.services.ai import (
        _summary_supported,
        ai_available,
        chat,
        get_ai_config,
        record_usage,
    )

    config = await get_ai_config(session)
    if not ai_available(config):
        return None, "AI 尚未配置"
    payload = [
        {
            "id": item["id"],
            "title": item["title"][:240],
            "text": item["text"][:800],
            "identity": item["identity"],
            "source": item["source"],
            "published_at": item["published_at"].isoformat(),
            "literal_keywords": item["keywords"],
        }
        for item in candidates[:30]
    ]
    system = (
        "你是《鸣潮》关键词通知编辑，只能根据输入候选生成 JSON："
        '{"items":[{"id":1,"summary":"80字以内事实摘要"}]}。'
        "只保留真正以用户关键词为主题或提供实质新信息的内容，最多5条；"
        "抽奖、签到、泛闲聊和仅顺带提及关键词的内容不要选；"
        "严格区分官方信息、社区观察和普通用户反馈，不得补充输入中没有的事实、数字或结论。"
    )
    prompt = (
        f"用户关键词：{json.dumps(keywords, ensure_ascii=False)}\n"
        f"候选内容：{json.dumps(payload, ensure_ascii=False)}"
    )
    started = time.monotonic()
    usage = None
    try:
        raw_out, usage = await chat(config, system, prompt, json_mode=True)
        parsed = json.loads(raw_out)
        selected_rows = parsed.get("items") or []
        by_id = {item["id"]: item for item in candidates}
        selected: list[dict] = []
        seen: set[int] = set()
        for row in selected_rows:
            content_id = int(row.get("id"))
            if content_id in seen or content_id not in by_id:
                continue
            candidate = by_id[content_id]
            summary = _plain(str(row.get("summary") or ""))[:160]
            evidence = f"{candidate['title']} {candidate['text']}"
            if not summary or not _summary_supported(summary, evidence):
                summary = candidate["text"][:120] or candidate["title"]
            selected.append({**candidate, "ai_summary": summary})
            seen.add(content_id)
            if len(selected) >= MAX_ITEMS:
                break
    except Exception as exc:  # noqa: BLE001
        await record_usage(
            session,
            task="notification_review",
            config=config,
            usage=usage,
            duration_ms=int((time.monotonic() - started) * 1000),
            ref_id=user_id,
            status="failed",
        )
        logger.warning("notification AI review for user %s failed: %r", user_id, exc)
        return None, "AI 生成失败，请稍后重试"
    await record_usage(
        session,
        task="notification_review",
        config=config,
        usage=usage,
        duration_ms=int((time.monotonic() - started) * 1000),
        ref_id=user_id,
    )
    return selected, None


def _format_time(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).astimezone(SHANGHAI).strftime("%Y/%m/%d %H:%M")


def render_email(kind: str, day: date, keywords: list[str], items: list[dict]) -> tuple[str, str]:
    label = "AI 精炼通知" if kind == "ai" else "关键词早报"
    subject = f"[AniSignal] {day:%Y/%m/%d} {label}"
    lines = [
        f"{day:%Y/%m/%d} {label}",
        f"关注关键词：{'、'.join(keywords)}",
        "",
    ]
    if not items:
        lines.extend(["本期没有发现明确匹配的新信息。", "系统只发送能够由原始内容支持的结果。"])
        return subject, "\n".join(lines)

    identity_labels = {
        "official": "官方信息",
        "media": "媒体/商家",
        "seeded": "送测内容",
        "user": "普通用户",
        "unknown": "社区观察",
    }
    for index, item in enumerate(items[:MAX_ITEMS], 1):
        summary = item.get("ai_summary") or item.get("summary") or item["title"]
        summary = _plain(str(summary))[:160]
        lines.extend(
            [
                f"{index}. [{identity_labels.get(item['identity'], '社区观察')}] {item['title']}",
                f"匹配关键词：{'、'.join(item['keywords'])}",
                f"摘要：{summary}",
                f"来源：{item.get('source') or item['platform']} · {_format_time(item['published_at'])}",
                f"原文：{item.get('url') or '暂无可访问链接'}",
                "",
            ]
        )
    lines.extend(["收到原因：你在 AniSignal 账号中心启用了邮件通知及以上关键词。"])
    return subject, "\n".join(lines)


async def create_delivery(
    session: AsyncSession,
    user_id: int,
    kind: str,
    source: str,
    *,
    day: date | None = None,
    dedupe_key: str | None = None,
) -> NotificationDelivery:
    row = NotificationDelivery(
        user_id=user_id,
        kind=kind,
        source=source,
        period_date=day or beijing_today(),
        dedupe_key=dedupe_key,
        status="pending",
        matched_keywords=[],
        content_ids=[],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def process_delivery(session: AsyncSession, delivery_id: int) -> dict:
    delivery = await session.get(NotificationDelivery, delivery_id)
    if delivery is None or delivery.status != "pending":
        return {"status": "skipped", "reason": "delivery_not_pending"}
    user = await session.get(User, delivery.user_id)
    if (
        user is None
        or user.status != "active"
        or not user.is_verified
        or not user.notification_enabled
    ):
        delivery.status = "skipped"
        delivery.error = "用户未启用通知或邮箱不可用"
        await session.commit()
        return {"status": "skipped", "reason": "user_not_eligible"}

    keywords = await enabled_keywords(session, user.id)
    if not keywords:
        delivery.status = "skipped"
        delivery.error = "没有启用中的关键词"
        await session.commit()
        return {"status": "skipped", "reason": "no_keywords"}

    if delivery.kind == "morning":
        start_at, end_at = morning_window(delivery.period_date)
    else:
        end_at = utcnow()
        start_at = end_at - timedelta(hours=24)
    candidates = await load_candidates(session, keywords, start_at, end_at)

    if delivery.kind == "ai":
        selected, ai_error = await ai_curate(session, user.id, keywords, candidates)
        if selected is None:
            delivery.status = "failed"
            delivery.error = ai_error
            await session.commit()
            return {"status": "failed", "reason": ai_error}
        items = await group_candidates(session, selected)
    else:
        trusted = [
            item
            for item in candidates
            if item["strength"] == "strong" and item["identity"] == "official"
        ]
        uncertain = [item for item in candidates if item not in trusted]
        if uncertain:
            reviewed, _ = await ai_curate(session, user.id, keywords, uncertain)
            if reviewed:
                trusted.extend(reviewed)
        items = await group_candidates(session, trusted)

    items = items[:MAX_ITEMS]
    if not items and delivery.source == "schedule":
        delivery.status = "skipped"
        delivery.error = "本期没有明确匹配的新信息"
        delivery.matched_keywords = keywords
        await session.commit()
        return {"status": "skipped", "reason": "no_matches"}

    subject, body = render_email(delivery.kind, delivery.period_date, keywords, items)
    sent = await send_email(
        user.email,
        subject,
        body,
        smtp=await _smtp(session),
    )
    delivery.subject = subject
    delivery.body = body
    delivery.matched_keywords = sorted(
        {keyword for item in items for keyword in item["keywords"]}
    ) or keywords
    delivery.content_ids = [content_id for item in items for content_id in item["content_ids"]]
    delivery.status = "sent" if sent else "failed"
    delivery.error = None if sent else "SMTP 发送失败，请查看服务端日志"
    delivery.sent_at = utcnow() if sent else None
    await session.commit()
    return {"status": delivery.status, "items": len(items)}


async def _smtp(session: AsyncSession) -> dict:
    from app.services.settings import get_effective_smtp

    return await get_effective_smtp(session)


async def run_scheduled_morning(session: AsyncSession) -> dict:
    """给主动启用通知的已验证用户发送当天早报，并向 OnePush 汇报批次结果。"""
    day = beijing_today()
    users = list(
        (
            await session.execute(
                select(User)
                .where(
                    User.notification_enabled.is_(True),
                    User.is_verified.is_(True),
                    User.status == "active",
                    select(UserKeyword.id)
                    .where(
                        UserKeyword.user_id == User.id,
                        UserKeyword.enabled.is_(True),
                    )
                    .exists(),
                )
                .order_by(User.id)
            )
        ).scalars()
    )
    stats = {"sent": 0, "skipped": 0, "failed": 0}
    for user in users:
        dedupe_key = f"morning:{day.isoformat()}:{user.id}"
        existing = await session.scalar(
            select(NotificationDelivery).where(NotificationDelivery.dedupe_key == dedupe_key)
        )
        if existing is not None:
            continue
        delivery = await create_delivery(
            session,
            user.id,
            "morning",
            "schedule",
            day=day,
            dedupe_key=dedupe_key,
        )
        result = await process_delivery(session, delivery.id)
        status = result.get("status")
        stats[status if status in stats else "failed"] += 1

    if users:
        from app.services.notifier import send_onepush
        from app.services.settings import get_onepush

        await send_onepush(
            f"AniSignal {day:%m/%d} 早报任务",
            f"成功 {stats['sent']}，无匹配或跳过 {stats['skipped']}，失败 {stats['failed']}。",
            await get_onepush(session),
        )
    return {"users": len(users), **stats}
