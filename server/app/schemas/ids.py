"""确定性外部内容与来源标识（纯哈希底层原语，不依赖采集器或服务层）。"""

from __future__ import annotations

import hashlib


def rss_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:48]


def web_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:48]


def git_item_id(repo: str, kind: str, ident: str) -> str:
    """生成跨 API / Atom 回退稳定的 external_id（长度始终 ≤ 64，兼容 RawContent 与 CustomFeedItem）。"""
    clean_ident = str(ident or "").strip()
    base = f"{repo.lower()}:{kind}:{clean_ident}"
    if len(base) <= 64:
        return base
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:48]
    return f"{kind}:{digest}"[:64]
