"""履约即时邮件：关注当前商品，发送前重新核验状态与公开证据。"""

from datetime import datetime, timedelta
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.config import get_settings
from app.models import NotificationDelivery, Product, ProductFulfillmentRevision, User
from app.services.email_templates import document
from app.services.fulfillment import build_fulfillment_snapshot, load_product_signal_data
from app.services.fulfillment_revisions import verified_fulfillment_state
from app.services.notifications import _plain, _safe_url, beijing_today, enabled_keywords, load_candidates

_AXIS_LABELS = {"official": "官方履约", "consumer": "消费者到货", "quality": "质量风险"}
_EMPTY_LABELS = {"official": "暂无已采纳官方履约信号", "consumer": "暂无已采纳自购到货信号", "quality": "暂无已采纳质量反馈"}


async def fulfillment_notification_payload(
    session: AsyncSession, user: User, revision: ProductFulfillmentRevision,
) -> dict | None:
    product = await session.get(Product, revision.product_id, populate_existing=True, with_for_update=True)
    if product is None or product.community != revision.community:
        return None
    latest_id = await session.scalar(select(ProductFulfillmentRevision.id).where(
        ProductFulfillmentRevision.product_id == product.id,
    ).order_by(ProductFulfillmentRevision.id.desc()).limit(1).with_for_update())
    if latest_id != revision.id:
        return None
    preferences = user.feed_preferences or {}
    if preferences.get("communities") and product.community not in preferences["communities"]:
        return None
    name = _plain(product.name)
    terms = [_plain(str(term)) for term in (product.watch_terms or [])]
    haystack = " ".join([name, *terms]).casefold()
    if any(keyword.casefold() in haystack for keyword in preferences.get("exclude_keywords", [])):
        return None
    keywords = [word for word in await enabled_keywords(session, user.id) if word.casefold() in haystack]
    if not keywords:
        return None

    data = await load_product_signal_data(session, product.id)
    current = await build_fulfillment_snapshot(session, product.id, signal_data=data)
    state = verified_fulfillment_state(current)
    if state != verified_fulfillment_state(revision.snapshot):
        return None
    ids = sorted({raw.id for signal, evidence, raw, _ in data[0]
                  if signal.review_status == "verified" and raw.community == product.community
                  and not raw.hidden and evidence.role != "excluded"})
    items = await load_candidates(
        session, None, datetime(1970, 1, 1), utcnow() + timedelta(seconds=1),
        content_ids=ids, community=product.community, preferences=preferences,
    )
    # 仍有采纳结论时至少保留一条符合偏好的公开证据；全部撤销时仍可发送更正。
    if (any(state[key]["verified_count"] for key in _AXIS_LABELS)
            or state["sample_count"] or state["observation_count"]) and not items:
        return None
    items = items[:5]
    labels = []
    for key, label in _AXIS_LABELS.items():
        count = state[key]["verified_count"]
        value = _plain(current[key]["state_label"]) if count else _EMPTY_LABELS[key]
        labels.append(f"{label}：{value}（已采纳 {count}）")
    labels.append(f"送测样品：已采纳 {state['sample_count']} 个独立来源")
    labels.append(f"实物展示：已采纳 {state['observation_count']} 个独立来源，购买方式未注明")
    before = verified_fulfillment_state(revision.previous_snapshot)
    correction = any(state[key]["verified_count"] < before[key]["verified_count"] for key in _AXIS_LABELS)
    correction = correction or state["sample_count"] < before["sample_count"]
    correction = correction or state["observation_count"] < before["observation_count"]
    lead = "原有履约判断已更正，以下为当前状态。" if correction else "已采纳履约信息有变化，以下为当前状态。"
    method = "自动判断仅依据原文证据；自动采纳不等于人工确认，后续证据变化会更新或更正。"
    method += f" 当前自动采纳 {int(current.get('automated_count') or 0)} 条、人工确认 {int(current.get('manual_count') or 0)} 条。"
    subject = f"AniSignal 履约{'更正' if correction else '变化'} · {name}"[:255]
    detail_url = f"{get_settings().public_base_url.rstrip('/')}/products/{product.slug}"
    lines = [name, lead, method, f"命中关注词：{'、'.join(keywords)}", *labels, "", "当前公开证据："]
    cards = []
    for item in items:
        title = _plain(item["title"])
        url = _safe_url(item.get("url"))
        lines.extend([f"• {title}", url or ""])
        link = f'<a href="{escape(url, quote=True)}">查看原文</a>' if url else ""
        cards.append(f"<li>{escape(title)} {link}</li>")
    if not items:
        lines.append("当前没有可展示的已采纳证据。")
    lines.extend(["", f"查看履约信号：{detail_url}", "可在账号中心关闭重要变化邮件或调整关注词。"])
    html = document(
        name, f"<p>{escape(lead)}</p><p>{escape(method)}</p><p>命中关注词：{escape('、'.join(keywords))}</p>"
        + "".join(f"<p>{escape(label)}</p>" for label in labels)
        + (f"<ul>{''.join(cards)}</ul>" if cards else "<p>当前没有可展示的已采纳证据。</p>")
        + f'<p><a href="{escape(detail_url, quote=True)}">查看履约信号</a></p>'
        + "<p>可在账号中心关闭重要变化邮件或调整关注词。</p>", eyebrow="履约变化",
    )
    return {"subject": subject, "body": "\n".join(lines), "html": html,
            "keywords": keywords, "content_ids": [item["id"] for item in items]}


async def prepare_user_fulfillment_notifications(session: AsyncSession, user: User) -> int:
    """调用方持有用户行锁，与事件通知一起提交游标和 outbox。"""
    if user.instant_fulfillment_cursor is None:
        user.instant_fulfillment_cursor = await session.scalar(
            select(ProductFulfillmentRevision.id).order_by(ProductFulfillmentRevision.id.desc()).limit(1).with_for_update()
        ) or 0
        return 0
    revisions = list(await session.scalars(select(ProductFulfillmentRevision).where(
        ProductFulfillmentRevision.id > user.instant_fulfillment_cursor,
    ).order_by(ProductFulfillmentRevision.id).limit(200)))
    if not revisions:
        return 0
    created = 0
    for revision in {row.product_id: row for row in revisions}.values():
        payload = await fulfillment_notification_payload(session, user, revision)
        if not payload:
            continue
        key = f"instant-fulfillment:{user.id}:{revision.product_id}:{revision.id}"
        if await session.scalar(select(NotificationDelivery.id).where(NotificationDelivery.dedupe_key == key)):
            continue
        session.add(NotificationDelivery(
            user_id=user.id, kind="instant", source="schedule", period_date=beijing_today(),
            dedupe_key=key, status="pending", subject=payload["subject"], body=payload["body"],
            html_body=payload["html"], matched_keywords=payload["keywords"], content_ids=payload["content_ids"],
        ))
        created += 1
    user.instant_fulfillment_cursor = revisions[-1].id
    return created
