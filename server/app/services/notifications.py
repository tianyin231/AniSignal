"""关键词邮件：候选筛选、AI 精炼、投递记录与每日批处理。"""

import json
import logging
import re
import time
from datetime import date, datetime, time as day_time, timedelta, timezone
from html import escape, unescape
from urllib.parse import urlparse, urlunparse

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
from app.services.email_templates import document, notice_email
from app.services.mailer import send_email

logger = logging.getLogger(__name__)

SHANGHAI = timezone(timedelta(hours=8))
MAX_ITEMS = 5

_LOW_SIGNAL_NOTIFICATION_PATTERNS = (
    re.compile(r"(?:reaction|反应|锐评|破防|泪崩)", re.IGNORECASE),
    re.compile(r"看.{0,16}(?:鸣潮|pv|动画|短片|剧情)", re.IGNORECASE),
    re.compile(r"(?:配队|伤害|矩阵|轮椅轴|手法|深塔|满星|速通)"),
    re.compile(r"(?:绘画过程|同人|手书|cosplay|cos|only|电台)", re.IGNORECASE),
)


def _is_low_signal_notification(item: dict) -> bool:
    """通知宁缺毋滥：过滤个人开箱、反应视频、攻略与二创。"""
    if item.get("identity") == "official":
        return False
    if item.get("category") in {"unboxing", "review", "arrived"}:
        return True
    title = _plain(str(item.get("title") or ""))
    return any(pattern.search(title) for pattern in _LOW_SIGNAL_NOTIFICATION_PATTERNS)


def _summary_respects_identity(item: dict, summary: str) -> bool:
    if item.get("identity") == "official":
        return True
    return re.search(r"(?:官方发布|官方宣布|正式官宣|官宣)", summary) is None


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


def _safe_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.scheme == "http" and parsed.hostname and parsed.hostname.endswith("hdslb.com"):
        parsed = parsed._replace(scheme="https")
    return urlunparse(parsed)


def _email_image_url(value: str | None) -> str | None:
    """邮件只引用远程图片；B 站原图过大时使用同源缩放地址。"""
    safe = _safe_url(value)
    if not safe:
        return None
    parsed = urlparse(safe)
    if parsed.hostname and parsed.hostname.endswith("hdslb.com") and "@" not in parsed.path:
        parsed = parsed._replace(path=f"{parsed.path}@672w_1e_1c.jpg")
        return urlunparse(parsed)
    return safe


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
        .where(RawContent.hidden.is_(False))
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
        normalized = version.normalized_json or {}
        media = normalized.get("media") if isinstance(normalized, dict) else []
        image = next(
            (
                safe
                for value in (media if isinstance(media, list) else [])
                if isinstance(value, str) and (safe := _email_image_url(value))
            ),
            None,
        )
        candidates.append(
            {
                "id": raw.id,
                "title": title,
                "text": (translated or text)[:800],
                "url": _safe_url(raw.url),
                "image": image,
                "platform": raw.platform,
                "source": source.name if source else None,
                "identity": source.identity if source else "unknown",
                "published_at": raw.published_at or raw.collected_at,
                "keywords": strong or weak,
                "strength": "strong" if strong else "weak",
                "category": version.category,
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
            if item.get("image") and not existing.get("image"):
                existing["image"] = item["image"]
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
            item["identity"] == "official",
            item["importance"],
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

    eligible = [item for item in candidates if not _is_low_signal_notification(item)]
    if not eligible:
        return [], None
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
            "category": item["category"],
            "published_at": item["published_at"].isoformat(),
            "literal_keywords": item["keywords"],
        }
        for item in eligible[:30]
    ]
    system = (
        "你是《鸣潮》资讯早报编辑，只能根据输入候选生成 JSON："
        '{"items":[{"id":1,"summary":"80字以内事实摘要"}]}。'
        "只保留真正以用户关键词为主题、且提供可核实新事实的内容，最多5条；没有合格内容就返回空数组，宁缺毋滥；"
        "抽奖、签到、泛闲聊、标题党、传闻预测、纯观看反应、攻略配队、伤害展示、开箱晒单、二创、情绪表达、无证据指控和仅顺带提及关键词的内容一律不要选；"
        "普通用户内容只有明确提供发售、预售、发货、延期或质量售后等新事实时才可入选；"
        "摘要要用一句自然中文说明发生了什么和新增了什么，不得照抄标题或视频简介，也不要写空泛评价；"
        "官方预告应明确说明官方发布了什么角色、版本、活动或商品信息；"
        "只有 identity=official 的候选可以写成官方发布或官宣；"
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
        by_id = {item["id"]: item for item in eligible}
        selected: list[dict] = []
        seen: set[int] = set()
        for row in selected_rows:
            content_id = int(row.get("id"))
            if content_id in seen or content_id not in by_id:
                continue
            candidate = by_id[content_id]
            summary = _plain(str(row.get("summary") or ""))[:160]
            evidence = f"{candidate['title']} {candidate['text']}"
            if (
                not summary
                or not _summary_supported(summary, evidence)
                or not _summary_respects_identity(candidate, summary)
            ):
                continue
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


def _item_summary(item: dict) -> str:
    summary = _plain(str(item.get("ai_summary") or item.get("summary") or ""))[:180]
    if summary:
        return summary
    title = _plain(str(item.get("title") or "这条更新"))[:100]
    source = _plain(str(item.get("source") or "鸣潮官方"))[:40]
    if item.get("identity") == "official":
        return f"{source}发布了「{title}」相关更新，详情以官方原文为准。"
    return f"发现一条与关注关键词相关的新内容：{title}。"


def _digest_intro(keywords: list[str], items: list[dict]) -> str:
    keyword_text = "、".join(keywords)
    if not items:
        return f"过去 24 小时，暂未发现与「{keyword_text}」明确相关的新信息。"
    official_count = sum(item.get("identity") == "official" for item in items)
    source_note = f"，其中官方信息 {official_count} 条" if official_count else ""
    return (
        f"过去 24 小时，围绕「{keyword_text}」确认了 {len(items)} 条有效更新{source_note}。"
        f"最值得关注的是：{_item_summary(items[0])}"
    )


def _render_digest_html(
    title: str,
    day: date,
    keywords: list[str],
    items: list[dict],
) -> str:
    intro = _digest_intro(keywords, items)
    keyword_chips = "".join(
        f'<span style="display:inline-block;margin:0 6px 6px 0;padding:4px 9px;border-radius:999px;background:#dbeafe;color:#1d4ed8;font-size:12px;font-weight:650;">{escape(keyword)}</span>'
        for keyword in keywords
    )
    parts = [
        f'<p class="email-muted" style="margin:0 0 8px;color:#64748b;font-size:13px;">{day:%Y/%m/%d} · 过去 24 小时</p>',
        f'<p class="email-text" style="margin:0 0 18px;color:#0f172a;font-size:17px;line-height:28px;font-weight:650;">{escape(intro)}</p>',
        f'<div style="margin:0 0 22px;">{keyword_chips}</div>',
    ]
    if not items:
        parts.append(
            '<div class="email-panel" style="padding:20px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;color:#475569;">'
            "系统只发送能够由原始内容支持的结果。本期没有为了凑数加入不确定内容。"
            "</div>"
        )

    identity_meta = {
        "official": ("官方信息", "#dbeafe", "#1d4ed8"),
        "media": ("媒体/商家", "#fef3c7", "#92400e"),
        "seeded": ("送测内容", "#ccfbf1", "#0f766e"),
        "user": ("普通用户", "#e2e8f0", "#475569"),
        "unknown": ("社区观察", "#e2e8f0", "#475569"),
    }
    for item in items[:MAX_ITEMS]:
        label, label_bg, label_color = identity_meta.get(
            item.get("identity"), identity_meta["unknown"]
        )
        item_title = _plain(str(item.get("title") or "未命名更新"))[:180]
        summary = _item_summary(item)
        source = _plain(str(item.get("source") or item.get("platform") or "未知来源"))[:80]
        url = _safe_url(item.get("url"))
        image = _email_image_url(item.get("image"))
        show_image = bool(image and (item.get("identity") == "official" or item.get("importance")))
        border = "#bfdbfe" if item.get("identity") == "official" else "#e2e8f0"
        card = [
            f'<div class="email-card" style="margin:0 0 16px;border:1px solid {border};border-radius:14px;overflow:hidden;background:#ffffff;">'
        ]
        if show_image:
            image_html = (
                f'<img src="{escape(image, quote=True)}" width="576" alt="{escape(item_title, quote=True)} 配图；若未显示请查看原文" '
                'style="display:block;width:100%;max-width:100%;height:auto;max-height:340px;object-fit:cover;border:0;">'
            )
            card.append(
                f'<a href="{escape(url or image, quote=True)}" style="display:block;text-decoration:none;">{image_html}</a>'
            )
        card.extend(
            [
                '<div style="padding:18px 20px;">',
                f'<div style="margin:0 0 10px;"><span style="display:inline-block;padding:3px 8px;border-radius:999px;background:{label_bg};color:{label_color};font-size:11px;font-weight:700;">{escape(label)}</span></div>',
                f'<h2 class="email-text" style="margin:0 0 9px;color:#0f172a;font-size:18px;line-height:27px;font-weight:750;">{escape(item_title)}</h2>',
                f'<p class="email-text" style="margin:0 0 13px;color:#334155;font-size:15px;line-height:25px;">{escape(summary)}</p>',
                f'<p class="email-muted" style="margin:0;color:#64748b;font-size:12px;line-height:20px;">{escape(source)} · {_format_time(item["published_at"])}</p>',
            ]
        )
        if url:
            card.append(
                f'<a class="email-button" href="{escape(url, quote=True)}" style="display:inline-block;margin-top:15px;padding:10px 15px;border-radius:9px;background:#2563eb;color:#ffffff;text-decoration:none;font-size:13px;font-weight:700;">查看原文</a>'
            )
            if show_image:
                card.append(
                    '<p class="email-muted" style="margin:9px 0 0;color:#94a3b8;font-size:11px;">图片未显示时，可通过上方按钮查看原始内容。</p>'
                )
        card.extend(["</div>", "</div>"])
        parts.extend(card)
    parts.append(
        '<p class="email-muted" style="margin:22px 0 0;color:#64748b;font-size:12px;line-height:20px;">'
        "收到原因：你在 AniSignal 账号中心启用了邮件通知及以上关键词。"
        "</p>"
    )
    return document(title, "".join(parts), eyebrow="ANISIGNAL DAILY", preheader=intro[:120])


def render_email(
    kind: str,
    day: date,
    keywords: list[str],
    items: list[dict],
) -> tuple[str, str, str]:
    label = "AI 精炼通知" if kind == "ai" else "关键词早报"
    subject = f"[AniSignal] {day:%Y/%m/%d} {label}"
    intro = _digest_intro(keywords, items)
    lines = [
        f"{day:%Y/%m/%d} {label}",
        f"关注关键词：{'、'.join(keywords)}",
        "",
        f"今日结论：{intro}",
        "",
    ]
    if not items:
        lines.extend(["本期没有发现明确匹配的新信息。", "系统只发送能够由原始内容支持的结果。"])
        body = "\n".join(lines)
        return subject, body, _render_digest_html(label, day, keywords, items)

    identity_labels = {
        "official": "官方信息",
        "media": "媒体/商家",
        "seeded": "送测内容",
        "user": "普通用户",
        "unknown": "社区观察",
    }
    for index, item in enumerate(items[:MAX_ITEMS], 1):
        summary = _item_summary(item)
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
    body = "\n".join(lines)
    return subject, body, _render_digest_html(label, day, keywords, items)


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

    if delivery.kind == "morning" and delivery.source == "manual":
        snapshot = await session.scalar(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.user_id == user.id,
                NotificationDelivery.kind == "morning",
                NotificationDelivery.source == "schedule",
                NotificationDelivery.period_date == delivery.period_date,
                NotificationDelivery.status == "sent",
                NotificationDelivery.id != delivery.id,
            )
            .order_by(NotificationDelivery.sent_at.desc())
        )
        if snapshot is not None and snapshot.subject and snapshot.body:
            html_body = snapshot.html_body or notice_email(
                snapshot.subject,
                snapshot.body,
                preheader="今日关键词早报重发",
            )
            sent = await send_email(
                user.email,
                snapshot.subject,
                snapshot.body,
                smtp=await _smtp(session),
                html_body=html_body,
            )
            delivery.subject = snapshot.subject
            delivery.body = snapshot.body
            delivery.html_body = html_body
            delivery.matched_keywords = snapshot.matched_keywords or keywords
            delivery.content_ids = snapshot.content_ids or []
            delivery.status = "sent" if sent else "failed"
            delivery.error = None if sent else "SMTP 发送失败，请查看服务端日志"
            delivery.sent_at = utcnow() if sent else None
            await session.commit()
            return {"status": delivery.status, "items": len(delivery.content_ids)}

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

    subject, body, html_body = render_email(
        delivery.kind, delivery.period_date, keywords, items
    )
    sent = await send_email(
        user.email,
        subject,
        body,
        smtp=await _smtp(session),
        html_body=html_body,
    )
    delivery.subject = subject
    delivery.body = body
    delivery.html_body = html_body
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
