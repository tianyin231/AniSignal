"""将旧引擎快照归入主数据库；原标识、判断依据和历史回执可追溯。"""

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

from sqlalchemy import select

from app.clock import utcnow
from app.collectors.rss import feed_time, rss_id
from app.models import (Base, CollectTarget, ContentReview, ContentVersion, Event,
    EventRevision, Evidence, NewsArchive, NewsGrouping, RawContent, Source)
from app.parsers.rss import parse_item
from app.services.ai_news import news_review_identity
from app.services.editorial_calls import fingerprint
from app.services.fingerprint import content_fingerprint

IMPORT_VERSION = "aihot-import-v1"
CORE = {"sources", "articles", "analyses", "publications", "facts", "stories", "fact_articles", "reports", "story_aliases", "settings"}


def date(value):
    parsed = feed_time(value or "")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed else None


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        header = json.loads(next(source))
        if header.get("format") != "anisignal-news-v1":
            raise ValueError("不是 AniSignal 资讯导出文件")
        for line in source:
            yield json.loads(line)


async def import_news(session, path):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    done = await session.get(NewsArchive, ("migration", "aihot"))
    if done and done.payload.get("complete"):
        if done.payload.get("sha256") != digest:
            raise ValueError("已完成过其他快照的迁移；避免旧数据覆盖上线后的修改")
        return {**done.payload, "replayed": True}
    tables, counts = defaultdict(list), defaultdict(int)
    for item in records(path):
        name = item["table"]
        counts[name] += 1
        if name in CORE:
            tables[name].append(item["row"])
    existing = list(await session.scalars(select(NewsArchive).where(NewsArchive.kind.in_(["article", "fact", "story", "source", "report"]))))
    archives = {(row.kind, row.key): row for row in existing}

    def archive(kind, key, payload, *, raw=None, event=None):
        identity = (kind, str(key))
        row = archives.get(identity)
        if row is None:
            row = NewsArchive(kind=kind, key=str(key), created_at=utcnow())
            archives[identity] = row
            session.add(row)
        row.payload = payload
        row.raw_content_id = raw
        row.event_id = event
        return row

    sources = {row["id"]: row for row in tables["sources"]}
    source_rows = {(row.platform, row.external_id): row for row in await session.scalars(select(Source))}
    source_ids = {row.id: row for row in source_rows.values()}
    targets = {row.target_value: row for row in await session.scalars(select(CollectTarget).where(CollectTarget.platform == "rss"))}
    for source in tables["sources"]:
        if source["kind"] != "rss":
            continue
        url = source["config"]["feedUrl"]
        target = targets.get(url)
        if target is None:
            target = CollectTarget(platform="rss", target_type="feed", target_value=url, community="ai",
                tier="core", min_views=0, co_terms=[], next_run_at=utcnow())
            session.add(target)
        target.display_name = source["name"]
        target.enabled = source["enabled"]
        target.interval_minutes = source["interval_minutes"]
        target.config = {"legacy_id": source["id"], "first_party": source["first_party"], "tier": source["tier"],
            "owner_key": source.get("owner_entity_id"), "participation_mode": source["participation_mode"],
            "site_fulltext": source["site_fulltext"], "syndicate_fulltext": source["syndicate_fulltext"]}
        target.last_success_at = date(source.get("last_ok_at"))
        target.last_run_at = date(source.get("last_fetch_at"))
        target.consecutive_failures = source.get("fail_count") or 0
        target.last_error = source.get("last_error")
        archive("source", source["id"], {"url": url})
    await session.flush()

    async def source_for(article):
        old = sources[article["source_id"]]
        platform = (article.get("raw") or {}).get("platform") or ("x" if article.get("x_post") else "rss")
        match = re.fullmatch(r"anisignal-[a-z_]+-(\d+)", old["id"])
        row = source_ids.get(int(match[1])) if match else None
        if row is not None and row.platform != platform:
            row = None
        external_id = rss_id(old["config"]["feedUrl"]) if old["kind"] == "rss" else str(article.get("author") or "import-" + rss_id(old["id"]))[:64]
        row = row or source_rows.get((platform, external_id))
        if row is None:
            row = Source(platform=platform, external_id=external_id, name=old["name"],
                identity="official" if old["first_party"] else "media", raw_json=old,
                first_seen_at=date(old.get("created_at")) or utcnow())
            session.add(row)
            await session.flush()
            source_rows[(platform, external_id)] = row
            source_ids[row.id] = row
        row.owner_key = (old.get("owner_entity_id") or row.owner_key or "")[:64] or None
        row.editorial_tier = "first_party" if old["first_party"] else "near_source" if old["tier"] == "T1_5" else "media"
        return row

    # 预载键和最新版本，避免逐条跨数据库往返查询。
    raws = list(await session.scalars(select(RawContent).where(RawContent.community == "ai")))
    raw_ids = {row.id: row for row in raws}
    raw_keys = {(row.platform, row.external_id): row for row in raws}
    foreign = list(await session.execute(select(RawContent.id, RawContent.platform, RawContent.external_id)
        .where(RawContent.community != "ai")))
    foreign_ids = {row.id for row in foreign}
    foreign_keys = {(row.platform, row.external_id) for row in foreign}
    versions = {}
    for row in await session.scalars(select(ContentVersion).join(RawContent, RawContent.id == ContentVersion.raw_content_id)
            .where(RawContent.community == "ai").order_by(ContentVersion.version)):
        versions[row.raw_content_id] = row
    reviews = {row.raw_content_id: row for row in await session.scalars(select(ContentReview)
        .join(RawContent, RawContent.id == ContentReview.raw_content_id).where(RawContent.community == "ai"))}
    publications = {row["article_id"]: row for row in tables["publications"]}
    analyses = {row["id"]: row for row in tables["analyses"]}
    article_map = {}
    for index, article in enumerate(tables["articles"]):
        old_id = article["id"]
        source = await source_for(article)
        meta = article.get("raw") or {}
        match = re.fullmatch(r"as-(\d+)", old_id)
        if match and int(match[1]) in foreign_ids:
            raise ValueError(f"旧资料 {old_id} 指向其他分区，停止迁移")
        raw = raw_ids.get(int(match[1])) if match else None
        if raw is not None and raw.community != "ai":
            raise ValueError(f"旧资料 {old_id} 指向其他分区，停止迁移")
        external_id = rss_id(article["url"]) if source.platform == "rss" else str(meta.get("externalId") or old_id)[:64]
        if (source.platform, external_id) in foreign_keys:
            raise ValueError(f"旧资料 {old_id} 与其他分区内容身份冲突，停止迁移")
        raw = raw or raw_keys.get((source.platform, external_id))
        version = versions.get(raw.id) if raw else None
        if raw is None:
            payload = {"id": external_id, "url": article["url"], "title": article["title"],
                "text": article.get("body_text") or article.get("excerpt") or "", "html": article.get("body_html") or "",
                "published": article.get("published_at"), "feed_url": sources[article["source_id"]]["config"].get("feedUrl", article["url"]),
                "source_name": source.name, "first_party": source.identity == "official",
                "media": [v["url"] for v in article.get("media", []) if v.get("kind") == "image"]}
            normalized = parse_item(payload) if source.platform == "rss" else None
            content_hash = content_fingerprint(normalized) if normalized else fingerprint(payload)
            raw = RawContent(platform=source.platform, external_id=external_id, community="ai", source_id=source.id,
                content_type="rss_item" if source.platform == "rss" else "post" if source.platform == "x" else "video",
                url=article["url"][:512], published_at=date(article.get("published_at")),
                collected_at=date(article.get("discovered_at")) or utcnow(),
                last_seen_at=date(article.get("updated_at")) or utcnow(), raw_json=payload,
                content_hash=content_hash, matched_keywords=[])
            session.add(raw)
            await session.flush()
            version = ContentVersion(raw_content_id=raw.id, version=1, title=article["title"],
                text=payload["text"], normalized_json=normalized.model_dump(mode="json") if normalized else {"media": payload["media"], "extra": {}},
                parser_version="rss-1" if source.platform == "rss" else IMPORT_VERSION, category="ai_news")
            session.add(version)
            await session.flush()
            raw_keys[(raw.platform, raw.external_id)] = raw
            raw_ids[raw.id], versions[raw.id] = raw, version
        if version is None:
            raise ValueError(f"资料 {raw.id} 缺少内容版本")
        publication = publications.get(old_id)
        analysis = analyses.get(publication.get("analysis_id")) if publication else None
        if publication:
            news = {"newsworthy": bool(publication["visibility"] == "public" and publication["eligible"]),
                "headline": publication["title"], "summary": publication.get("summary") or "",
                "kind": "report", "topics": publication.get("tags") or [],
                "content_hash": raw.content_hash, "content_version": version.version,
                "imported_publication": {key: publication.get(key) for key in
                    ["selected", "score", "visible_after", "seat", "revision", "fact_id", "story_id", "backfill", "selected_ready_at"]}}
            review = reviews.get(raw.id)
            if review is None:
                review = ContentReview(raw_content_id=raw.id)
                session.add(review)
                reviews[raw.id] = review
            review.relevant = bool(not analysis or analysis["relevance"] == "pass")
            review.promotional, review.violation = False, False
            review.quality_score = int(publication.get("score") or 0)
            review.news, review.model = news, (analysis or {}).get("model") or "imported"
            review.updated_at = date(publication.get("updated_at")) or utcnow()
            raw.hidden = publication["visibility"] != "public" or not review.relevant
        archive("article", old_id, {"article": article, "publication": publication,
            "content_version": version.version, "content_hash": raw.content_hash}, raw=raw.id)
        article_map[old_id] = raw.id
        if index % 500 == 0:
            await session.flush()

    groups = {row.raw_content_id: row for row in await session.scalars(select(NewsGrouping))}
    existing_evidence = set((await session.execute(select(Evidence.event_id, Evidence.raw_content_id))).all())
    facts, roots = {}, {}
    for fact in sorted(tables["facts"], key=lambda row: row["id"]):
        old = archives.get(("fact", str(fact["id"])))
        event = await session.get(Event, old.event_id) if old and old.event_id else None
        if event is None:
            event = Event(title=fact["title"][:255], community="ai", category="ai_news", identity="media",
                summary_status="imported", first_seen_at=date(fact.get("created_at")) or utcnow(),
                last_updated_at=date(fact.get("updated_at")) or utcnow())
            session.add(event)
            await session.flush()
            session.add(EventRevision(event_id=event.id, actor="engine", note="从旧资讯引擎迁入事件和原始证据"))
        facts[fact["id"]] = event.id
        roots.setdefault(fact["story_id"], event.id)
        archive("fact", fact["id"], fact, event=event.id)
        if fact.get("public_id"):
            archive("fact", fact["public_id"], fact, event=event.id)
    for member in tables["fact_articles"]:
        rid, eid = article_map.get(member["article_id"]), facts.get(member["fact_id"])
        if rid is None or eid is None or member.get("role") == "mention":
            continue
        if (eid, rid) not in existing_evidence:
            session.add(Evidence(event_id=eid, raw_content_id=rid, role="support"))
            existing_evidence.add((eid, rid))
    for old_id, publication in publications.items():
        rid, eid = article_map.get(old_id), facts.get(publication.get("fact_id"))
        if rid is None or eid is None or not reviews.get(rid):
            continue
        raw, version, review = raw_ids[rid], versions[rid], reviews[rid]
        group = groups.get(rid)
        if group is None:
            group = NewsGrouping(raw_content_id=rid)
            session.add(group)
            groups[rid] = group
        group.event_id, group.story_id = eid, roots.get(publication.get("story_id"), eid)
        group.content_hash, group.content_version = raw.content_hash, version.version
        group.review_key = fingerprint(news_review_identity(review.news))
        group.relation, group.related_event_ids = "UNRELATED", []
        group.reason, group.prompt_version = "保留旧引擎的事件归属与发布判断", IMPORT_VERSION
        group.created_at = date(publication.get("selected_ready_at") or publication.get("updated_at")) or utcnow()
        group.updated_at = date(publication.get("updated_at")) or utcnow()
    for story in tables["stories"]:
        archive("story", story["public_id"], story, event=roots.get(story["id"]))
    for alias in tables["story_aliases"]:
        target = next((s for s in tables["stories"] if s["id"] == alias.get("story_id")), None)
        if target:
            archive("story", alias.get("alias") or alias.get("public_id"), target, event=roots.get(target["id"]))
    for report in tables["reports"]:
        archive("report", report["kind"] + "/" + report["key"], report)
    await session.commit()
    if any(setting["key"] == "anisignal:unlimited-llm" for setting in tables["settings"]):
        from app.services.editorial_calls import get_policy, POLICY_KEY
        from app.services.settings import set_setting
        await set_setting(session, POLICY_KEY, {**await get_policy(session), "calls_unlimited": True})
    # 原始分析、修订和调用回执归档在同一 MySQL；流式分批写入，不把全部历史装进内存。
    from app.services.news_import_history import import_history
    await import_history(session, path, records)
    result = {"sha256": digest, "complete": True, "counts": dict(counts), "articles": len(article_map),
        "facts": len(facts), "rss_targets": len(targets) + sum(s["kind"] == "rss" and s["config"]["feedUrl"] not in targets for s in tables["sources"]),
        "completed_at": utcnow().isoformat()}
    archive("migration", "aihot", result)
    await session.commit()
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--create-tables", action="store_true", help="仅用于本地演练；生产先运行 Alembic")
    args = parser.parse_args()
    from app.db import SessionLocal, engine
    try:
        if args.create_tables:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        async with SessionLocal() as session:
            print(json.dumps(await import_news(session, args.snapshot), ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
