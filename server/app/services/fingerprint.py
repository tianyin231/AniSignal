"""内容指纹：标准化结果的稳定哈希（不含指标与采集时间）。

指标随时间变化，若参与指纹会导致每次采集都生成新版本；
指纹只覆盖"内容本身变了"的字段，指标变化只进快照表。
"""

import hashlib
import json
import re

from app.schemas.content import NormalizedContent

_URL_SCHEME = re.compile(r"^(?:https?:)?//")
_BILIBILI_IMAGE_SHARD = re.compile(r"^i\d\.hdslb\.com/")


def canonical_media(url: str) -> str:
    """同一张图的不同写法归一：B 站会轮换 CDN 分片（i0/i1/i2）和 http/https。"""
    return _BILIBILI_IMAGE_SHARD.sub("i0.hdslb.com/", _URL_SCHEME.sub("", url))


def content_body(title: str | None, text: str | None, media: list | None, extra: dict | None = None) -> tuple:
    """判断两个版本是不是同一份内容的比较键。

    比指纹更宽：指纹还覆盖昵称、链接和图片地址原文，这些变化要记下来，但不算内容更新。
    截断标记保留在键里，X 的正文补全要作为新版本重新审核。
    """
    images = tuple(canonical_media(item) for item in media or [] if isinstance(item, str))
    return (title or "", (text or "").strip(), images, bool((extra or {}).get("truncated")))


def content_fingerprint(content: NormalizedContent) -> str:
    stable = {
        "platform": content.platform,
        "content_type": content.content_type,
        "external_id": content.external_id,
        "author": content.author.model_dump(mode="json", exclude={"identity"})
        if content.author
        else None,
        "title": content.title,
        "text": content.text,
        "url": content.url,
        "media": content.media,
    }
    if content.platform == "x":
        # 补全状态变化也需要新版本，重新审核先前只拿到片段的原帖。
        stable["text_truncated"] = bool(content.extra.get("truncated"))
    canonical = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
