"""机器人查询的关键词拆分与匹配。

模型常把多个词塞进一次查询，整串原样匹配几乎必然落空；这里按空格和标点拆成需要同时命中的词。
不维护停用词表：哪个词是重点交给模型判断，查不到时由调用方报告每个词各自的命中数，让模型换词重查。
"""

import re

from sqlalchemy import and_, or_

from app.communities import COMMUNITIES
from app.models import ContentTranslation, ContentVersion

_SPLIT = re.compile(r"[\s,，、;；/|｜+＋&＆×「」『』《》“”\"'()（）\[\]【】<>〈〉]+")
_NAMES = {key: {name.casefold() for name in (meta.label, meta.bare_name, *meta.extra_names, *meta.source_aliases) if name}
          for key, meta in COMMUNITIES.items()}
# 连写的查询里只拆出分区注册表里的中文游戏名；英文缩写太短，放进长串里容易误拆。
_JOINED_NAMES = sorted({name for meta in COMMUNITIES.values() for name in (meta.label, meta.bare_name, *meta.extra_names)
                        if len(name) >= 2 and re.search(r"[一-鿿]", name)}, key=len, reverse=True)


def search_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _SPLIT.split(query or ""):
        for name in _JOINED_NAMES:
            if name in raw and name != raw:
                raw = raw.replace(name, f" {name} ")
        for term in raw.split():
            if term.casefold() not in seen:
                seen.add(term.casefold())
                terms.append(term)
    return terms


def split_communities(terms: list[str]) -> tuple[list[str], set[str]]:
    """游戏名转为分区范围，其余词留给正文匹配。"""
    text_terms: list[str] = []
    communities: set[str] = set()
    for term in terms:
        keys = {key for key, names in _NAMES.items() if term.casefold() in names}
        if keys:
            communities |= keys
        else:
            text_terms.append(term)
    return text_terms, communities


def matches_terms(item: dict, terms: list[str]) -> bool:
    sources = [item, *(item.get("sources") or [])]
    text = " ".join(str(source.get(key) or "") for source in sources
                    for key in ("title", "source_title", "text", "summary", "name")).casefold()
    names = _NAMES.get(item.get("community") or "", set())
    return all(term.casefold() in text or term.casefold() in names for term in terms)


def search_condition(terms: list[str]):
    """在数据库里先按词收窄，避免为一次查询把整个时间窗口的正文流回应用。"""
    def contains(term: str):
        pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return or_(ContentVersion.title.ilike(pattern, escape="\\"), ContentVersion.text.ilike(pattern, escape="\\"),
                   ContentTranslation.text.ilike(pattern, escape="\\"))
    return and_(*(contains(term) for term in terms))
