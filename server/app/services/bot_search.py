"""机器人查询的关键词拆分与匹配。

模型常把多个词塞进一次查询（「明日方舟 名创优品 联动」或「明日方舟名创优品联动」），
整串原样匹配几乎必然落空；这里拆成需要同时命中的词，游戏名按分区匹配，口水词不参与。
"""

import re

from sqlalchemy import and_, or_

from app.communities import COMMUNITIES
from app.models import ContentTranslation, ContentVersion

# 修饰查询的词不能当成正文必须出现的词；类型词只在没有别的实词时才单独用来检索。
GENERIC = ("最新", "最近", "相关", "内容", "情况", "进展", "信息", "消息", "新闻", "资讯", "动态", "官方")
TYPE_WORDS = ("联动", "活动", "公告")
FILLER = GENERIC + TYPE_WORDS
_TRIM = "「」『』《》“”\"'()（）[]【】<>〈〉"
_SPLIT = re.compile(r"[\s,，、;；/|｜+＋&＆×" + re.escape(_TRIM) + r"]+")
_NAMES = {key: {name.casefold() for name in (meta.label, meta.bare_name, *meta.extra_names, *meta.source_aliases) if name}
          for key, meta in COMMUNITIES.items()}
# 连写的中文查询里只拆出中文游戏名；英文缩写太短，放进长串里容易误拆。
_JOINED_NAMES = sorted({name for meta in COMMUNITIES.values() for name in (meta.label, meta.bare_name, *meta.extra_names)
                        if len(name) >= 2 and re.search(r"[一-鿿]", name)}, key=len, reverse=True)


def _peel(part: str, peeled: list[str]) -> str:
    """剥掉词头词尾的修饰词（「名创优品联动」→「名创优品」），剥下的词记入 peeled。"""
    changed = True
    while changed and part:
        changed = False
        if part in FILLER:
            peeled.append(part)
            return ""
        for word in FILLER:
            if part.startswith(word):
                peeled.append(word)
                part, changed = part[len(word):], True
            elif part.endswith(word):
                peeled.append(word)
                part, changed = part[:-len(word)], True
    return part


def _unique(words: list[str]) -> list[str]:
    seen: set[str] = set()
    return [word for word in words if not (word.casefold() in seen or seen.add(word.casefold()))]


def search_terms(query: str) -> list[str]:
    """拆出需要同时命中的词；只剩修饰词时返回空列表，按不带关键词处理。"""
    parts: list[str] = []
    for raw in _SPLIT.split(query or ""):
        part = raw
        for name in _JOINED_NAMES:
            if name in part and name != part:
                part = part.replace(name, f" {name} ")
        parts += part.split()
    peeled: list[str] = []
    terms = [core for part in parts if (core := _peel(part, peeled))]
    return _unique(terms or [word for word in peeled if word in TYPE_WORDS])


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
