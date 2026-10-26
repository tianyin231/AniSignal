"""商品频道的已采纳履约变化；复用现有修订和发送预算，不调用摘要模型。"""

from sqlalchemy import select

from app.config import get_settings
from app.models import Product, ProductFulfillmentRevision
from app.services.fulfillment import build_fulfillment_snapshot, load_product_signal_data
from app.services.fulfillment_revisions import verified_fulfillment_state
from app.services.notifications import _plain

AXES = {"official": "官方履约", "consumer": "消费者到货", "quality": "质量风险"}
EMPTY = {"official": "暂无已采纳官方履约信号", "consumer": "暂无已采纳自购到货信号", "quality": "暂无已采纳质量反馈"}


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
    correction = any(state[axis]["verified_count"] < previous[axis]["verified_count"] for axis in AXES)
    correction = correction or any(state[key] < previous[key] for key in ("sample_count", "observation_count"))
    points = [f"{label}：{_plain(current[axis]['state_label']) if state[axis]['verified_count'] else EMPTY[axis]}（已采纳 {state[axis]['verified_count']}）"
              for axis, label in AXES.items()]
    points += [f"送测样品：{state['sample_count']} 个独立来源", f"购买方式未注明的实物展示：{state['observation_count']} 个独立来源"]
    title = f"{_plain(product.name)} · 履约{'更正' if correction else '变化'}"
    text = "；".join(points) + "。已采纳信息仍可随证据修正。"
    return {"id": f"fulfillment:{revision.id}", "content_ids": [], "community": product.community,
            "title": title, "text": text, "summary": text, "key_points": points,
            "url": f"{get_settings().public_base_url.rstrip('/')}/products/{product.slug}",
            "source": "商品履约档案", "platform": "product", "identity": "system", "content_type": "fulfillment",
            "published_at": revision.created_at, "collected_at": revision.created_at, "importance": True,
            "strength": "strong", "keywords": [], "image": None, "event_id": None,
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
