"""商品频道的已采纳履约变化；复用现有修订和发送预算，不调用摘要模型。"""

import re

from sqlalchemy import select

from app.communities import COMMUNITIES
from app.config import get_settings
from app.models import Product, ProductFulfillmentRevision
from app.services.fulfillment import build_fulfillment_snapshot, load_product_signal_data
from app.services.fulfillment_revisions import verified_fulfillment_state
from app.services.notifications import _plain

AXES = ("official", "consumer", "quality")
# 群消息只说发生了什么；“已采纳 N”等审核口径留在商品页。
OFFICIAL = {"presale_open": "开启预售", "balance_open": "开启尾款", "delayed": "宣布延期",
            "shipping_started": "开始发货", "aftersales_notice": "发布售后说明"}
QUALITY = {"isolated_issue": "出现质量反馈", "repeated_issue": "多个来源反馈质量问题"}
# 详情只取商品档案的原文字段，不另行推断价格或日期；按状态取，不依赖展示文字。
DETAILS = {
    "presale_open": (("price_note", "价格"), ("presale_at", "预售"), ("ship_note", "发货")),
    "balance_open": (("balance_window", "尾款"), ("ship_note", "发货")),
    "delayed": (("original_ship_at", "原定发货"), ("ship_note", "发货")),
    "shipping_started": (("ship_note", "发货"),),
}


def describe_change(previous: dict, state: dict, current: dict | None = None) -> tuple[list[str], list[str], bool]:
    """返回（变化、补充说明、是否更正）；同一状态只多了证据条数不算新进展。"""
    events, notes = [], []
    correction = any(state[axis]["verified_count"] < previous[axis]["verified_count"] for axis in AXES)
    correction = correction or any(state[key] < previous[key] for key in ("sample_count", "observation_count"))
    official, before = state["official"], previous["official"]
    if not official["verified_count"] and before["verified_count"]:
        old = OFFICIAL.get(before["state"])
        notes.append(f"此前「{old}」的判断已撤回" if old else "此前的官方履约判断已撤回")
    elif official["verified_count"] and official["state"] != before["state"]:
        events.append(OFFICIAL.get(official["state"]) or _plain(((current or {}).get("official") or {}).get("state_label") or "官方履约有更新"))
    consumer, before = state["consumer"], previous["consumer"]
    if consumer["verified_count"] > before["verified_count"]:
        count = consumer["verified_count"]
        events.append("首位玩家晒出到货" if count == 1 else f"已有 {count} 位玩家晒出到货")
    elif consumer["verified_count"] < before["verified_count"]:
        notes.append("部分到货反馈已撤回" if consumer["verified_count"] else "此前的到货反馈已撤回")
    if state["sample_count"] > previous["sample_count"]:
        events.append("出现送测样品")
    if state["observation_count"] > previous["observation_count"] and not events:
        events.append("出现实物开箱")
    quality, before = state["quality"], previous["quality"]
    if quality["state"] != before["state"] and quality["state"] in QUALITY:
        events.append(QUALITY[quality["state"]])
    elif quality["verified_count"] > before["verified_count"] and quality["state"] in QUALITY:
        events.append("质量反馈增加")
    elif quality["verified_count"] < before["verified_count"]:
        notes.append("部分质量反馈已撤回" if quality["verified_count"] else "此前的质量反馈已撤回")
    if any(event in QUALITY.values() or event == "质量反馈增加" for event in events):
        notes.append("质量反馈只代表已收集到的个例，不代表整体情况")
    return events, notes, correction


def short_name(name: str, community: str) -> str:
    meta = COMMUNITIES.get(community)
    for game in sorted(meta.game_names if meta else (), key=len, reverse=True):
        if name.startswith(game) and len(name) > len(game) + 1:
            return name[len(game):].lstrip(" ·｜|：:-")
    return name


def _detail(value: str | None, label: str) -> str:
    text = re.split(r"[。！!；;\n]", _plain(value or "").strip(), maxsplit=1)[0].strip(" ，,")
    if not text or len(text) > 32:
        return ""
    return text if re.search(r"[：:]", text) else f"{label}：{text}"


def product_details(product: Product, official_state: str | None) -> list[str]:
    fields = DETAILS.get(official_state or "", ())
    return list(dict.fromkeys(filter(None, (_detail(getattr(product, field), label) for field, label in fields))))


async def revision_item(session, revision):
    product = await session.get(Product, revision.product_id, populate_existing=True)
    if not product or product.community != revision.community:
        return None
    latest = await session.scalar(select(ProductFulfillmentRevision.id).where(
        ProductFulfillmentRevision.product_id == product.id).order_by(ProductFulfillmentRevision.id.desc()).limit(1))
    if latest != revision.id:
        return None
    data = await load_product_signal_data(session, product.id)
    current = await build_fulfillment_snapshot(session, product.id, signal_data=data)
    state = verified_fulfillment_state(current)
    if state != verified_fulfillment_state(revision.snapshot):
        return None
    visible = {raw.id for signal, evidence, raw, _ in data[0]
               if signal.review_status == "verified" and not raw.hidden and evidence.role != "excluded"
               and raw.community == product.community}
    if (any(state[axis]["verified_count"] for axis in AXES) or state["sample_count"] or state["observation_count"]) and not visible:
        return None
    previous = verified_fulfillment_state(revision.previous_snapshot)
    events, notes, correction = describe_change(previous, state, current)
    if not events and not notes:
        # 同一状态只多了一条证据，对群聊不算新进展；商品页照常更新。
        return None
    name = _plain(product.name)
    short = short_name(name, product.community)
    series = re.match(r"(?P<series>\S{2,20}系列)\s+(?P<item>\S.*)$", short)
    change = "、".join(events[:2]) if events else "履约判断有更正"
    official = state["official"]
    official_changed = official["verified_count"] and official["state"] != previous["official"]["state"]
    details = product_details(product, official["state"] if official_changed else None)
    summary = "。".join([*notes, "；".join(details)] if details else notes)
    summary = summary + "。" if summary else ""
    text = f"{name}{change}。{summary}"
    return {"id": f"fulfillment:{revision.id}", "content_ids": [], "community": product.community,
            "title": f"{short} {change}", "text": text, "summary": summary or text,
            "key_points": [change, *notes, *details],
            "url": f"{get_settings().public_base_url.rstrip('/')}/products/{product.slug}",
            "source": "商品履约档案", "platform": "product", "identity": "system", "content_type": "fulfillment",
            "published_at": revision.created_at, "collected_at": revision.created_at, "importance": True,
            "strength": "strong", "keywords": [], "image": None, "event_id": None,
            "product": {"name": short, "series": series["series"] if series else "",
                        "item": series["item"] if series else short, "change": change,
                        "details": details, "notes": notes, "correction": correction},
            "fulfillment_evidence_ids": sorted(visible), "watch_terms": product.watch_terms or [],
            "fulfillment_revision": revision.id, "notification_channels": [f"{product.community}:product"]}


async def candidates(session, target, keywords, start_at, end_at, *, collected_after=None):
    communities = [key.split(":", 1)[0] for key in target.config.get("channels", []) if key.endswith(":product")]
    if not communities:
        return []
    rows = list(await session.scalars(select(ProductFulfillmentRevision).where(
        ProductFulfillmentRevision.community.in_(communities),
        ProductFulfillmentRevision.created_at >= max(start_at, collected_after or start_at),
        ProductFulfillmentRevision.created_at < end_at,
    ).order_by(ProductFulfillmentRevision.id.desc()).limit(50)))
    output = []
    for revision in rows:
        item = await revision_item(session, revision)
        if item:
            haystack = " ".join([item["title"], item["text"], *item["watch_terms"]]).casefold()
            item["keywords"] = [word for word in keywords or [] if word.casefold() in haystack]
            if not keywords or item["keywords"]:
                output.append(item)
    return output


async def valid_item(session, item):
    revision = await session.get(ProductFulfillmentRevision, item["fulfillment_revision"])
    return bool(revision and await revision_item(session, revision))
