"""统一资讯管理；旧管理 URL 保留，操作主系统的数据和任务队列。"""

from datetime import timedelta
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.clock import utcnow
from app.collectors.rss import public_feed_url, rss_id
from app.collectors.base import CollectorError
from app.models import (AiUsage, CollectTarget, ContentReview, ContentVersion, EditorialReceipt,
    NewsArchive, NewsGrouping, RawContent, Source)
from app.services.news_import import date
from app.services import native_news


def source_view(row):
    config = row.config or {}
    return {"id": config.get("legacy_id") or f"rss-{row.id}", "name": row.display_name or row.target_value,
        "kind": "rss", "tier": config.get("tier", "T2"), "enabled": row.enabled,
        "participation_mode": config.get("participation_mode", "editorial"), "interval_minutes": row.interval_minutes,
        "owner_entity_id": config.get("owner_key"), "updated_at": row.updated_at,
        "health": "failing" if row.consecutive_failures else "ok" if row.last_success_at else "pending",
        "last_ok_at": row.last_success_at, "last_error": row.last_error,
        "config": {"feedUrl": row.target_value}, "items_7d": 0, "selected_30d": 0}


async def source_rows(session):
    rows = list(await session.scalars(select(CollectTarget).where(CollectTarget.platform == "rss").order_by(CollectTarget.id)))
    return rows


async def status(session):
    rows = await source_rows(session)
    count = await session.scalar(select(func.count(RawContent.id)).where(RawContent.community == "ai")) or 0
    reviewed = await session.scalar(select(func.count(ContentReview.raw_content_id)).join(RawContent, RawContent.id == ContentReview.raw_content_id)
        .where(RawContent.community == "ai")) or 0
    grouped = await session.scalar(select(func.count(NewsGrouping.raw_content_id))) or 0
    calls = await session.scalar(select(func.count(AiUsage.id)).where(AiUsage.created_at >= utcnow() - timedelta(days=1))) or 0
    return {"enabled": True, "read_enabled": True, "engine": "anisignal", "sources": {
        "rows": [source_view(row) for row in rows], "totals": {"total": len(rows),
            "enabled": sum(row.enabled for row in rows), "failing": sum(bool(row.consecutive_failures) for row in rows)}},
        "pending_review": max(0, count - reviewed), "grouped": grouped, "calls_24h": calls}


async def find_source(session, key):
    row = next((row for row in await source_rows(session) if source_view(row)["id"] == key), None)
    if row is None:
        raise HTTPException(404, "RSS 来源不存在")
    return row


async def save_source(session, body, key=None):
    patch = body.get("patch", {}) if key else body
    row = await find_source(session, key) if key else None
    if row and body.get("version") and date(body["version"]) != row.updated_at:
        raise HTTPException(409, "来源已更新，请刷新")
    url = (patch.get("config") or {}).get("feedUrl") or (row.target_value if row else "")
    try:
        await public_feed_url(url)
    except CollectorError as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(url) > 255:
        raise HTTPException(422, "RSS 地址超过 255 字符")
    duplicate = await session.scalar(select(CollectTarget).where(CollectTarget.platform == "rss", CollectTarget.target_value == url))
    if duplicate and (not row or duplicate.id != row.id):
        raise HTTPException(409, "该 RSS 已存在")
    if row is None:
        key = body.get("id") or "rss-" + rss_id(url)[:16]
        if not re.fullmatch(r"[a-z0-9-]{1,80}", key):
            raise HTTPException(422, "来源标识格式不正确")
        if any(source_view(existing)["id"] == key for existing in await source_rows(session)):
            raise HTTPException(409, "来源标识已存在")
        row = CollectTarget(platform="rss", target_type="feed", community="ai", tier="core", min_views=0,
            enabled=True, co_terms=[], config={"legacy_id": key}, next_run_at=utcnow())
        session.add(row)
    config = dict(row.config or {})
    tier = patch.get("tier", config.get("tier", "T2"))
    participation = patch.get("participation_mode", config.get("participation_mode", "editorial"))
    if tier not in {"T1", "T1_5", "T2"} or participation not in {"editorial", "hot_signal", "isolated"}:
        raise HTTPException(422, "来源级别或参与方式不正确")
    name = str(patch.get("name") or row.display_name or "").strip()
    interval = int(patch.get("interval_minutes", row.interval_minutes or 60))
    if not name or len(name) > 200 or not 5 <= interval <= 1440:
        raise HTTPException(422, "检查来源名称和采集间隔")
    config.update(tier=tier, first_party=tier == "T1", participation_mode=participation,
        owner_key=patch.get("owner_entity_id", config.get("owner_key")), site_fulltext=False)
    row.target_value, row.display_name, row.interval_minutes = url, name, interval
    row.config, row.updated_at = config, utcnow()
    if "enabled" in patch:
        row.enabled = bool(patch["enabled"])
    source = await session.scalar(select(Source).where(Source.platform == "rss", Source.external_id == rss_id(url)))
    if source is None:
        source = Source(platform="rss", external_id=rss_id(url))
        session.add(source)
    source.name, source.owner_key = name, config.get("owner_key")
    source.identity = "official" if tier == "T1" else "media"
    source.editorial_tier = {"T1": "first_party", "T1_5": "near_source", "T2": "media"}[tier]
    source.raw_json = {**(source.raw_json or {}), "participation_mode": participation}
    await session.commit()
    return {"created": not bool(body.get("patch")), "source": source_view(row)}


async def content_row(session, key):
    alias = await session.get(NewsArchive, ("article", key))
    match = re.fullmatch(r"(?:as|content)-(\d+)", key)
    rid = alias.raw_content_id if alias else int(match[1]) if match else None
    row = await session.get(RawContent, rid) if rid is not None else None
    if row is None or row.community != "ai":
        raise HTTPException(404)
    version = await session.scalar(select(ContentVersion).where(ContentVersion.raw_content_id == row.id)
        .order_by(ContentVersion.version.desc()).limit(1))
    return row, version, await session.get(ContentReview, row.id)


async def content_search(session, q):
    latest = select(func.max(ContentVersion.version)).where(ContentVersion.raw_content_id == RawContent.id).correlate(RawContent).scalar_subquery()
    query = select(RawContent, ContentVersion, ContentReview, Source).join(ContentVersion,
        (ContentVersion.raw_content_id == RawContent.id) & (ContentVersion.version == latest)).outerjoin(
        ContentReview, ContentReview.raw_content_id == RawContent.id).outerjoin(Source, Source.id == RawContent.source_id).where(RawContent.community == "ai")
    if q:
        alias = await session.get(NewsArchive, ("article", q))
        match = re.fullmatch(r"(?:as|content)-(\d+)", q)
        rid = alias.raw_content_id if alias else int(match[1]) if match else -1
        query = query.where(or_(ContentVersion.title.icontains(q, autoescape=True), RawContent.url.icontains(q, autoescape=True),
            RawContent.id == rid))
    rows = (await session.execute(query.order_by(RawContent.collected_at.desc()).limit(50))).all()
    aliases = await native_news.article_aliases(session, [row.id for row, _, _, _ in rows])
    return {"rows": [{"id": aliases[raw.id].key if raw.id in aliases else f"content-{raw.id}", "title": version.title,
        "source": source.name if source else "来源未署名", "processing_state": "reviewed" if review else "pending",
        "visibility": "withdrawn" if raw.hidden else "public", "selected": bool((review.news or {}).get("imported_publication", {}).get("selected")) if review else False}
        for raw, version, review, source in rows]}


async def request(session, path, *, method, params, body, queue=None):
    parts = path.split("/")
    if parts[0] == "sources":
        if method == "GET":
            return {"source": source_view(await find_source(session, parts[1]))} if len(parts) > 1 else (await status(session))["sources"]
        if method == "POST" and len(parts) == 1:
            return await save_source(session, body)
        if method == "PATCH" and len(parts) == 2:
            return await save_source(session, body, parts[1])
        if method == "POST" and len(parts) == 3 and parts[2] == "fetch":
            target = await find_source(session, parts[1])
            return {"job_id": await queue.enqueue_collect(target.id)}
    if parts[0] == "content":
        if len(parts) == 1 and method == "GET":
            return await content_search(session, str(params.get("q") or "")[:200])
        raw, version, review = await content_row(session, parts[1])
        if len(parts) == 2 and method == "GET":
            receipts = list(await session.scalars(select(EditorialReceipt).where(EditorialReceipt.ref_id == raw.id)
                .order_by(EditorialReceipt.id.desc()).limit(10)))
            return {"article": {"id": parts[1], "title": version.title, "processing_state": "reviewed" if review else "pending", "processing_error": None},
                "publication": {"title": (review.news or {}).get("headline", version.title), "summary": (review.news or {}).get("summary", ""),
                    "visibility": "withdrawn" if raw.hidden else "public"} if review else None,
                "override": {"version": version.version}, "analyses": [{"id": raw.id, "score": review.quality_score,
                    "relevance": "pass" if review.relevant and not raw.hidden else "block", "receipts": [
                        {"id": r.id, "status": r.status} for r in receipts]}] if review else []}
        if method == "POST" and parts[2] == "visibility":
            if not body.get("reason") or body.get("version") != version.version:
                raise HTTPException(409, "请刷新内容版本并填写原因")
            raw.hidden = body.get("visibility") != "public"
            session.add(NewsArchive(kind="visibility", key=uuid4().hex, raw_content_id=raw.id,
                payload={"hidden": raw.hidden, "reason": body["reason"], "content_version": version.version}, created_at=utcnow()))
            if not raw.hidden and review and review.news:
                review.relevant, review.promotional, review.violation = True, False, False
                review.news = {**review.news, "newsworthy": True}
            await session.commit()
            return {"ok": True}
        if method == "POST" and parts[2] == "rerun":
            if body.get("step") == "analyze":
                return {"job_id": await queue.enqueue_review([raw.id])}
            if body.get("step") == "group":
                grouping = await session.get(NewsGrouping, raw.id)
                if grouping:
                    grouping.prompt_version = ""
                if review and review.news:
                    review.news = {k: v for k, v in review.news.items() if k not in {"imported_publication", "selection"}}
                await session.commit()
                return {"job_id": await queue.enqueue_editorial()}
            raise HTTPException(422, "支持重新评判或归组")
    raise HTTPException(404)
