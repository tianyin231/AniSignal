"""把现有平台采集器的资料逐版本送入原版引擎；确认成功才推进水位。"""

from sqlalchemy import select

from app.models import RawContent, ContentReview, ContentVersion, Source
from app.clock import utcnow
from app.services import aihot
from app.services.settings import get_setting, set_setting


def payload(raw, version, source, *, cutover, imported_at=None, review=None):
    normalized = version.normalized_json or {}
    source_id = f"anisignal-{raw.platform}-{source.id if source else 'unknown'}"
    tier = "T1" if source and (source.identity == "official" or source.editorial_tier == "first_party") else "T2"
    if source and source.editorial_tier in {"first_party", "near_source", "media"}:
        tier = {"first_party": "T1", "near_source": "T1_5", "media": "T2"}[source.editorial_tier]
    images = normalized.get("media") or []
    news = review.news if review else None
    current_review = bool(news and news.get("content_hash") == raw.content_hash
        and news.get("content_version") == version.version)
    decision = None
    if current_review:
        decision = {
            "approved": bool(review.relevant and not review.promotional and not review.violation and news.get("newsworthy")),
            "headline": news.get("headline") or version.title or "",
            "summary": news.get("summary") or "", "topics": news.get("topics") or [],
            "model": review.model, "reviewedAt": aihot.iso(review.updated_at),
        }
    return {"id": f"as-{raw.id}", "source": {"id": source_id, "name": (source.name if source else None) or raw.platform,
            "tier": tier, "owner": source.owner_key if source else None},
        "platform": raw.platform, "externalId": raw.external_id, "url": raw.url,
        "title": version.title or "", "text": version.text or "", "author": source.external_id if source else None,
        "bodyComplete": not bool((normalized.get("extra") or {}).get("truncated")),
        "publishedAt": aihot.iso(raw.published_at) if raw.published_at else None,
        "discoveredAt": imported_at if version.id <= cutover and imported_at else aihot.iso(raw.collected_at),
        # hidden 也表示本地待审；只有已审核的隐藏状态才是需要继承的下架决定。
        # 显式人工下架仍由独立的 visibility 接口同步，不在这里清除。
        "platformCollectedAt": aihot.iso(raw.collected_at), "hidden": bool(raw.hidden and current_review),
        "review": decision,
        "media": [{"kind": "image", "url": image} for image in images if isinstance(image, str) and image.startswith("http")][:30],
        "backfill": "anisignal-initial-import" if version.id <= cutover else None}


async def sync(session, *, limit=50):
    if not aihot.bridge_enabled():
        return {"status": "disabled"}
    state = await get_setting(session, "aihot_bridge")
    if state is None:
        from sqlalchemy import func
        cutover = await session.scalar(select(func.max(ContentVersion.id)).join(RawContent)
            .where(RawContent.community == "ai")) or 0
        state = {"cursor": 0, "live_cursor": cutover, "cutover": cutover, "imported_at": aihot.iso(utcnow())}
        await set_setting(session, "aihot_bridge", state)
    from sqlalchemy import func
    from sqlalchemy.orm import aliased
    other = aliased(ContentVersion)
    latest = select(func.max(other.id)).where(other.raw_content_id == RawContent.id).correlate(RawContent).scalar_subquery()
    query = (select(RawContent, ContentVersion, Source, ContentReview)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .outerjoin(Source, Source.id == RawContent.source_id)
        .outerjoin(ContentReview, ContentReview.raw_content_id == RawContent.id)
        .where(RawContent.community == "ai", ContentVersion.id == latest))
    # 新资料先行；首次导入的历史分批补齐，不能挡住实时采集。
    live = (await session.execute(query.where(ContentVersion.id > state.get("live_cursor", state["cutover"]))
        .order_by(ContentVersion.id).limit(limit))).all()
    history = (await session.execute(query.where(ContentVersion.id > state["cursor"], ContentVersion.id <= state["cutover"])
        .order_by(ContentVersion.id).limit(max(0, limit - len(live))))).all()
    rows = [(True, r) for r in live] + [(False, r) for r in history]
    count = 0
    for is_live, (raw, version, source, review) in rows:
        if raw.url:
            await aihot.request("/internal/anisignal/materials", method="POST",
                body=payload(raw, version, source, cutover=state["cutover"], imported_at=state.get("imported_at"), review=review))
        state = {**state, "live_cursor" if is_live else "cursor": version.id}
        # 失败重试同一版本，upsertMaterial 提供幂等性。
        await set_setting(session, "aihot_bridge", state)
        count += 1
    return {"synced": count, **state}


async def visibility(content_id, hidden):
    if aihot.bridge_enabled():
        await aihot.request(f"/internal/anisignal/materials/as-{content_id}/visibility", method="POST", body={"hidden": hidden})
