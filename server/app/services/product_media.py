"""官方商品图只保存原站 URL；AI 看图时仅在内存中读取，不落盘或代理展示。"""

import asyncio
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit

from app.services.urls import is_public_http_url


class _ArticleImages(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.images: list[dict] = []
        self.context = ""

    def handle_data(self, data):
        self.context = (self.context + data)[-240:]

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        attrs = dict(attrs)
        src = attrs.get("src") or attrs.get("data-src") or attrs.get("data-original")
        if not src:
            return
        url = urljoin(self.base_url, src)
        if is_public_http_url(url):
            self.images.append({"url": url, "caption": attrs.get("alt") or self.context[-160:]})


def official_image_candidates(version, base_url: str | None) -> list[dict]:
    parser = _ArticleImages(base_url or "")
    parser.feed(version.text or "")
    candidates = [*parser.images, *[
        {"url": url, "caption": version.title or "公告配图"}
        for url in (version.normalized_json or {}).get("media", []) if isinstance(url, str)
    ]]
    seen, result = set(), []
    for item in candidates:
        url = item["url"]
        if not is_public_http_url(url) or url in seen:
            continue
        seen.add(url)
        result.append({"id": len(result) + 1, "url": url, "caption": item["caption"]})
        if len(result) == 8:
            break
    return result


async def merch_message(payload: dict, images: list[dict]):
    """周边公告附上最多四张图供 AI 对应商品；普通新闻只做文本分析。"""
    text = json.dumps({**payload, "images": images}, ensure_ascii=False)
    if not images or not re.search(r"周边|联名|预售|发售|手办|键盘|merch|pre.?order", payload["title"] + payload["text"], re.I):
        return text
    from app.services.curation import _cover_data_url

    # AI 只需辨认商品，B站原图常达数 MB；使用官方 CDN 缩放且保留入库原图 URL。
    def inference_url(url: str) -> str:
        parsed = urlsplit(url)
        if re.fullmatch(r"i\d\.hdslb\.com", parsed.hostname or "") and parsed.path.startswith("/bfs/"):
            return parsed._replace(path=f"{parsed.path.split('@')[0]}@960w.webp").geturl()
        return url

    data_urls = await asyncio.gather(*[_cover_data_url(inference_url(item["url"])) for item in images[:4]])
    content = [{"type": "text", "text": text}]
    for item, data_url in zip(images, data_urls):
        if data_url:
            content.extend([{"type": "text", "text": f"官方配图 #{item['id']}"},
                            {"type": "image_url", "image_url": {"url": data_url, "detail": "low"}}])
    return content if len(content) > 1 else text
