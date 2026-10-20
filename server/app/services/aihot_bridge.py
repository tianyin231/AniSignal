"""把现有平台采集器的资料逐版本送入原版引擎；确认成功才推进水位。"""

from sqlalchemy import select

from app.models import RawContent, ContentVersion, Source
from app.clock import utcnow
from app.services import aihot
from app.services.settings import get_setting, set_setting


def payload(raw, version, source, *, cutover, imported_at=None):
    normalized = version.normalized_json or {}
    source_id = f"anisignal-{raw.platform}-{source.id if source else 'unknown'}"
    tier = "T1" if source and (source.identity == "official" or source.editorial_tier == "first_party") else "T2"
    if source and source.editorial_tier in {"first_party", "near_source", "media"}:
        tier = {"first_party": "T1", "near_source": "T1_5", "media": "T2"}[source.editorial_tier]
    images = normalized.get("media") or []
    return {"id": f"as-{raw.id}", "source": {"id": source_id, "name": (source.name if source else None) or raw.platform,
            "tier": tier, "owner": source.owner_key if source else None},
        "platform": raw.platform, "externalId": raw.external_id, "url": raw.url,
        "title": version.title or "", "text": version.text or "", "author": source.external_id if source else None,
        "publishedAt": aihot.iso(raw.published_at) if raw.published_at else None,
        "discoveredAt": imported_at if version.id <= cutover and imported_at else aihot.iso(raw.collected_at),
        "platformCollectedAt": aihot.iso(raw.collected_at), "hidden": raw.hidden,
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
    query = (select(RawContent, ContentVersion, Source)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .outerjoin(Source, Source.id == RawContent.source_id)
        .where(RawContent.community == "ai", ContentVersion.id == latest))
    # 新资料先行；首次导入的历史分批补齐，不能挡住实时采集。
    live = (await session.execute(query.where(ContentVersion.id > state.get("live_cursor", state["cutover"]))
        .order_by(ContentVersion.id).limit(limit))).all()
    history = (await session.execute(query.where(ContentVersion.id > state["cursor"], ContentVersion.id <= state["cutover"])
        .order_by(ContentVersion.id).limit(max(0, limit - len(live))))).all()
    rows = [(True, r) for r in live] + [(False, r) for r in history]
    count = 0
    for is_live, (raw, version, source) in rows:
        if raw.url:
            await aihot.request("/internal/anisignal/materials", method="POST",
                body=payload(raw, version, source, cutover=state["cutover"], imported_at=state.get("imported_at")))
        state = {**state, "live_cursor" if is_live else "cursor": version.id}
        # 失败重试同一版本，upsertMaterial 提供幂等性。
        await set_setting(session, "aihot_bridge", state)
        count += 1
    return {"synced": count, **state}


async def visibility(content_id, hidden):
    if aihot.bridge_enabled():
        await aihot.request(f"/internal/anisignal/materials/as-{content_id}/visibility", method="POST", body={"hidden": hidden})
