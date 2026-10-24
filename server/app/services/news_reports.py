"""资讯日报读取及历史刊物兼容；只读取已生成结果，不在网页请求中调用模型。"""

from copy import deepcopy
from datetime import date
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.models import NewsArchive, NewsEdition
from app.services import native_news
from app.services.news_details import resolve_article


async def issues(session, kind):
    if kind not in {"daily", "weekly", "monthly"}:
        raise HTTPException(404)
    result = {row.key.split("/", 1)[1]: row.payload for row in await session.scalars(
        select(NewsArchive).where(NewsArchive.kind == "report", NewsArchive.key.startswith(kind + "/")))}
    if kind == "daily":
        for row in await session.scalars(select(NewsEdition).where(NewsEdition.community == "ai")):
            key = str(row.day)
            if key in result:
                continue
            aliases = await native_news.article_aliases(session, [item["id"] for item in row.content.get("items", [])])
            citations = [{"itemId": aliases[item['id']].key if item["id"] in aliases else f"content-{item['id']}", "title": item["headline"], "summary": item["summary"],
                "sourceName": item["source_name"], "sourceUrl": item.get("url"), "sourceVersions": item.get("source_versions")}
                for item in row.content.get("items", [])]
            result[key] = {"kind": kind, "key": key, "generated_at": row.created_at.isoformat(),
                "content": {"lead": None, "highlights": [], "sections": [{"label": "本期精选", "items": citations}], "flashes": []}}
    return result


async def citation(session, value):
    item = deepcopy(value) if isinstance(value, dict) else {"itemId": value}
    key = item.get("itemId") or item.get("id")
    item["itemId"] = key
    item.setdefault("title", "已撤下的报道")
    item.setdefault("summary", None)
    item.setdefault("sourceName", "")
    item.setdefault("sourceUrl", "")
    try:
        row, alias = await resolve_article(session, key)
        versions = item.get("sourceVersions")
        expected = versions.get(str(row.raw.id)) if versions else None
        if expected and (row.raw.content_hash, row.version.version) != tuple(expected[:2]):
            raise HTTPException(404)
        if not versions and alias and alias.payload.get("content_hash") != row.raw.content_hash:
            raise HTTPException(404)
        item["available"] = True
        if not isinstance(value, dict):
            item.update(title=row.news["headline"], summary=row.news["summary"],
                sourceName=row.source.name if row.source else "来源未署名", sourceUrl=row.raw.url)
    except HTTPException:
        item["available"] = False
        item["summary"], item["sourceUrl"] = None, ""
    item.pop("sourceVersions", None)
    return item


async def report(session, kind, key):
    available = await issues(session, kind)
    keys = sorted(available)
    if key not in available:
        raise HTTPException(404, "本期尚未发布")
    row = available[key]
    content = deepcopy(row["content"])
    sections = [{**section, "items": [await citation(session, item) for item in section.get("items", [])]}
        for section in content.get("sections", [])]
    for section in sections:
        if any(not item["available"] for item in section["items"]):
            section["summary"] = None
    lead = content.get("lead")
    if content.get("leadItemId") and not (await citation(session, content["leadItemId"]))["available"]:
        lead = None
    index = keys.index(key)
    label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}[kind]
    return {"kind": kind, "key": key, "title": f"AI {label} · {key}", "issueNumber": index + 1,
        "generatedAt": row.get("generated_at") or row.get("created_at"), "lead": lead,
        "overview": content.get("overview"), "highlights": [await citation(session, v) for v in content.get("highlights", [])],
        "sections": sections, "flashes": [await citation(session, v) for v in content.get("flashes", [])],
        "prev": keys[index - 1] if index else None, "next": keys[index + 1] if index + 1 < len(keys) else None}


async def request(session, path):
    kind, _, key = path.partition("/")
    available = await issues(session, kind)
    keys = sorted(available)
    if key == "latest-page":
        return {"report": await report(session, kind, keys[-1]) if keys else None}
    if key:
        return await report(session, kind, key)
    return {"items": [{"key": key, "title": f"AI { {'daily': '日报', 'weekly': '周报', 'monthly': '月报'}[kind]} · {key}",
        "issueNumber": index + 1, "count": sum(len(v.get("items", [])) for v in available[key]["content"].get("sections", []))
            + len(available[key]["content"].get("highlights", [])) + len(available[key]["content"].get("flashes", []))}
        for index, key in reversed(list(enumerate(keys)))]}


async def rss():
    data = await native_news.feed(selected=True, limit=30)
    base = get_settings().public_base_url.rstrip("/")
    root = Element("rss", version="2.0")
    channel = SubElement(root, "channel")
    SubElement(channel, "title").text = "AniSignal · AI 精选"
    SubElement(channel, "link").text = base + "/ai"
    SubElement(channel, "description").text = "AI 资讯与来源"
    for item in data["items"]:
        entry = SubElement(channel, "item")
        SubElement(entry, "title").text = item["headline"]
        SubElement(entry, "link").text = base + item["path"]
        SubElement(entry, "guid", isPermaLink="false").text = str(item["id"])
        SubElement(entry, "description").text = item["summary"]
    return tostring(root, encoding="utf-8", xml_declaration=True)
