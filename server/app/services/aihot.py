"""统一资讯网关：对外保持发布接口协议，内部统一读取主数据库。"""

import re

from app.config import get_settings


def unified() -> bool:
    return bool(getattr(get_settings(), "news_unified_enabled", False))


def enabled() -> bool:
    return unified()


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
    from app.services import native_news

    result = await native_news.request(path, method=method, params=params, body=body, raw=raw, missing_ok=missing_ok)
    return result if raw or result is None else public_media(result)


async def sources():
    from app.services import native_news

    return await native_news.sources()


async def feed(*, q="", topic="", kind="all", hours=168, sort="time", selected=False, limit=20, offset=0,
               exclude_platforms=()):
    from app.services import native_news

    return await native_news.feed(
        q=q, topic=topic, kind=kind, hours=hours, sort=sort, selected=selected,
        limit=limit, offset=offset, exclude_platforms=exclude_platforms,
    )


async def edition(day=None):
    if day:
        report = await request(f"/api/site/reports/daily/{day}", missing_ok=True)
    else:
        latest = await request("/api/site/reports/daily/latest-page")
        report = latest.get("report")
    if not report:
        return {"day": None, "items": [], "engine": "anisignal"}
    citations = (
        report.get("highlights", [])
        + [i for section in report.get("sections", []) for i in section["items"]]
        + report.get("flashes", [])
    )
    seen, items = set(), []
    for citation in citations:
        cid = citation.get("id") or citation.get("itemId")
        fact = citation.get("factId") or cid
        if not cid or fact in seen or not citation.get("available", True):
            continue
        seen.add(fact)
        items.append({
            "id": "aihot:" + cid,
            "path": "/ai/items/" + cid,
            "headline": citation["title"],
            "summary": citation.get("summary") or "",
            "sources": [{"source_name": citation.get("sourceName"), "url": citation.get("sourceUrl")}],
            "topics": [],
            "score": None,
        })
    return {
        "day": report["key"],
        "created_at": report.get("generatedAt"),
        "items": items,
        "path": "/ai/reports/daily/" + report["key"],
        "report": report,
        "engine": "anisignal",
    }


async def bot_items(keywords, start_at, end_at, *, collected_after=None):
    from app.services import native_news

    return await native_news.bot_items(keywords, start_at, end_at, collected_after=collected_after)


async def validate_bot_items(items):
    from app.services import native_news

    return await native_news.validate_bot_items(items)
