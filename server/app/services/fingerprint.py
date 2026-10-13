"""内容指纹：标准化结果的稳定哈希（不含指标与采集时间）。

指标随时间变化，若参与指纹会导致每次采集都生成新版本；
指纹只覆盖"内容本身变了"的字段，指标变化只进快照表。
"""

import hashlib
import json

from app.schemas.content import NormalizedContent


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
