"""旧图片地址兼容：只转发已保存的公开文章图片。"""

from fastapi import HTTPException
import httpx
from sqlalchemy import select

from app.collectors.rss import public_feed_url
from app.models import NewsArchive


async def image(session, params):
    url = params.get("u", "")
    if not url:
        raise HTTPException(404)
    # URL 来自导入的图片数组，而不是把公开接口变成任意网络代理。
    candidates = await session.scalars(select(NewsArchive).where(NewsArchive.kind == "article",
        NewsArchive.payload["article"]["media"].as_string().contains(url, autoescape=True)).limit(10))
    allowed = any(any(m.get("url") == url and m.get("kind") == "image"
        for m in (row.payload.get("article") or {}).get("media", [])) for row in candidates)
    if not allowed:
        raise HTTPException(404)
    await public_feed_url(url)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", url) as response:
            mime = response.headers.get("content-type", "").split(";", 1)[0]
            if response.status_code != 200 or not mime.startswith("image/"):
                raise HTTPException(404)
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 5 * 1024 * 1024:
                    raise HTTPException(413)
    return httpx.Response(200, content=bytes(content), headers={"content-type": mime, "cache-control": "public, max-age=3600"})
