"""AIHOT 原版发布层的网关。这里只转换平台格式，不作第二轮评分或发布判定。"""

from datetime import datetime, timezone
import re

import httpx
from fastapi import HTTPException

from app.config import get_settings


def enabled() -> bool:
    config = get_settings()
    return bool(config.aihot_url and config.aihot_read_enabled)


def bridge_enabled() -> bool:
    config = get_settings()
    return bool(config.aihot_url and config.aihot_token)


def iso(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None) if value else None


def public_media(value):
    if isinstance(value, str):
        # 只改本站相对图片地址；外部原文 URL 必须逐字保留。
        return re.sub(r'(^|[\"\' ,])(/api/img-proxy\?)', r'\1/api/ai-news/media?', value)
    if isinstance(value, list):
        return [public_media(v) for v in value]
    if isinstance(value, dict):
        return {k: public_media(v) for k, v in value.items()}
    return value


async def request(path: str, *, method="GET", params=None, body=None, raw=False, missing_ok=False):
    config = get_settings()
    if not config.aihot_url:
        raise HTTPException(503, "AIHOT 引擎尚未配置")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            result = await client.request(method, config.aihot_url.rstrip("/") + path,
                headers={"X-Anisignal-Key": config.aihot_token}, params=params, json=body)
    except httpx.RequestError as exc:
        raise HTTPException(503, "AIHOT 引擎暂时不可用") from exc
    if result.status_code == 404 and missing_ok:
        return None
    if result.status_code in (301, 302, 307, 308):
        # 仅追随原引擎内部路径，绝不把桥接密钥送到外部地址。
        target = result.headers.get("location", "")
        if target.startswith("/api/site/") and not target.startswith("//"):
            return await request(target, raw=raw, missing_ok=missing_ok)
    if result.status_code >= 400:
        raise HTTPException(result.status_code if result.status_code in (400, 404, 409, 422) else 503,
            "AIHOT 请求失败，请检查引擎运行状态")
    return result if raw else public_media(result.json())


def news_item(item):
    item_id = item["id"]
    return {"id": "aihot:" + item_id, "engine_id": item_id, "path": "/ai/items/" + item_id,
        "headline": item["title"], "summary": item.get("summary") or "", "original_title": item.get("originalTitle") or "",
        "kind": "report", "topics": item.get("tags") or [], "platform": item.get("platform") or ("x" if item.get("channel") == "x" else "rss"),
        "source_name": item.get("source", {}).get("name") or "原始来源", "source_role": "original",
        "verification": "pending", "published_at": item.get("publishedAt"), "latest_at": item.get("timelineAt"),
        "url": item.get("links", {}).get("original"), "references": [], "sources": [], "developments": [],
        "source_count": 1, "report_count": 1, "score": item.get("score"), "selected": item.get("selected", False),
        "reason": item.get("reason"), "image": None, "engine": "aihot"}


async def sources():
    data = await request("/internal/anisignal/sources")
    rows = data["rows"]
    for page in range(2, (data["totals"]["total"] + 99) // 100 + 1):
        rows += (await request("/internal/anisignal/sources", params={"page": page}))["rows"]
    return [{"id": r["id"], "name": r["name"], "platform": "rss" if r["kind"] == "rss" else "bridge",
        "status": "active" if (r.get("last_ok_at") or r["kind"] == "external" and r.get("items_7d")) and not r.get("fail_count") else "delayed" if r.get("fail_count") else "pending",
        "last_success_at": r.get("last_ok_at"), "interval_minutes": r["interval_minutes"]}
        for r in rows if r["enabled"]]


async def feed(*, q="", topic="", kind="all", hours=168, sort="time", selected=False, limit=20, offset=0):
    if sort == "hot":
        data = await request("/api/site/hot")
        items = []
        for entry in data["entries"]:
            story = entry["story"]
            item = {"id": "aihot-story:" + story["publicId"], "path": "/ai/stories/" + story["publicId"],
                "headline": story["title"], "summary": entry.get("summary") or entry.get("latest") or "",
                "topics": [], "platform": "rss", "source_name": "、".join(entry.get("sourceNames") or []),
                "source_count": entry["sourceCount"], "report_count": entry.get("participantCount", 0),
                "heat": entry["heat"], "trend": entry["trend"], "developments": [], "sources": [],
                "references": [], "participant_count": entry.get("participantCount", 0),
                "published_at": None, "latest_at": data.get("computedAt"), "kind": "report", "verification": "pending",
                "image": (entry.get("cover") or {}).get("url"), "engine": "aihot"}
            if q and q.casefold() not in (item["headline"] + item["summary"]).casefold():
                continue
            items.append(item)
        return {"items": items[offset:offset+limit], "total": len(items), "topics": [], "sources": await sources(), "engine": "aihot"}
    data = await request("/internal/anisignal/feed", params={"q": q, "topic": topic, "hours": hours,
        "selected": str(selected).lower(), "limit": limit, "offset": offset})
    return {"items": [news_item(i) for i in data["items"]],
        "total": data["total"], "topics": data.get("topics", []), "sources": await sources(), "engine": "aihot"}


async def edition(day=None):
    if day:
        report = await request(f"/api/site/reports/daily/{day}", missing_ok=True)
    else:
        latest = await request("/api/site/reports/daily/latest-page")
        report = latest.get("report")
    if not report:
        return {"day": None, "items": [], "engine": "aihot"}
    citations = report.get("highlights", []) + [i for section in report.get("sections", []) for i in section["items"]] + report.get("flashes", [])
    seen, items = set(), []
    for citation in citations:
        cid = citation.get("id") or citation.get("itemId")
        if not cid or cid in seen or not citation.get("available", True):
            continue
        seen.add(cid)
        items.append({"id": "aihot:"+cid, "path": "/ai/items/"+cid, "headline": citation["title"],
            "summary": citation.get("summary") or "", "sources": [{"source_name": citation.get("sourceName"), "url": citation.get("sourceUrl")}],
            "topics": [], "score": None})
    return {"day": report["key"], "created_at": report.get("generatedAt"), "items": items,
        "path": "/ai/reports/daily/" + report["key"], "report": report, "engine": "aihot"}


async def bot_items(keywords, start_at, end_at, *, collected_after=None):
    params = {"start": iso(start_at), "end": iso(end_at)}
    if collected_after:
        params["after"] = iso(collected_after)
    if get_settings().aihot_bot_after:
        params["cutover"] = get_settings().aihot_bot_after
    rows = await request("/internal/anisignal/bot/items", params=params)
    result = []
    for row in rows:
        text = " ".join(str(row.get(k) or "") for k in ("title", "summary", "original_title"))
        if keywords and not any(k.casefold() in text.casefold() for k in keywords):
            continue
        item_id = "aihot:" + row["id"]
        news = {"engine": "aihot", "engine_id": row["id"], "revision": row["revision"], "fact_id": row["fact_id"],
            "headline": row["title"], "summary": row["summary"] or "", "kind": "report", "published_at": row["published_at"]}
        result.append({"id": item_id, "community": "ai", "title": row["title"], "source_title": row["original_title"] or row["title"],
            "text": row["summary"] or "", "summary": row["summary"] or "", "key_points": [row["summary"] or ""], "news": news,
            "url": row["url"], "image": None, "platform": "x" if row["channel"] == "x" else "rss", "content_type": "rss_item",
            "source": row["source_name"], "identity": "official" if row["first_party"] else "user",
            "published_at": timestamp(row["published_at"]), "collected_at": timestamp(row["updated_at"]),
            "keywords": keywords or [], "strength": "strong", "category": "ai_news", "event_id": None,
            "importance": False, "content_ids": [item_id]})
    return result


async def validate_bot_items(items):
    payload = [{"id": i["news"]["engine_id"], "revision": i["news"]["revision"]} for i in items]
    return {"aihot:"+i for i in await request("/internal/anisignal/bot/validate", method="POST", body=payload)}
