"""wikitext → 纯文本 / 结构化字段的清洗工具。

两条路都用它：
  * 结构化路：infobox 模板参数 → dict
  * 文本路：正文 → 去噪 → 表格转行 → 分节分段
"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable

import mwparserfromhell

from .config import NAV_TEMPLATE_PREFIXES

# ---- 常量 ----
_CAT_PREFIXES = ("分类:", "category:", "Category:")
_FILE_PREFIXES = ("file:", "image:", "文件:", "檔案:", "档案:")

# 整个标签连内容一起丢掉的 HTML 标签
_DROP_TAGS = {
    "ref", "references", "nowiki", "gallery", "timeline", "syntaxhighlight",
    "source", "math", "code", "pre", "imagemap", "inputbox", "quiz",
}
# 只保留内部文本的标签（解开）
_UNWRAP_TAGS = {
    "div", "span", "p", "u", "b", "i", "em", "strong", "s", "strike", "del",
    "sub", "sup", "small", "big", "font", "center", "blockquote", "li", "ul",
    "ol", "dl", "dd", "dt", "h1", "h2", "h3", "h4", "h5", "h6", "abbr", "cite",
    "kbd", "mark", "q", "samp", "tt", "var", "wbr", "main", "section", "article",
    "header", "footer", "aside", "figure", "figcaption", "details", "summary",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
}
# noinclude/includeonly/onlyinclude 这类包裹层：保留内部内容
_TRANSPARENT_TAGS = {"noinclude", "includeonly", "onlyinclude"}

# 需要解析成可读文本的"展示型"模板
_VALUE_TEMPLATES = {
    "强化值", "营养价值", "强化值列表", "小图标", "小图标2", "小图标3", "小图标2R",
    "小图标2不推荐", "小图标2无文字", "小圆角图片", "划掉重说", "黑幕", "文案引号",
    "装备名链接", "装备大图标", "装备外观", "技能图标", "舰娘头像", "舰船名称对照表",
    "模板", "颜色", "转化颜色", "变色",
}


def parse_wikitext(text: str) -> mwparserfromhell.wikicode.Wikicode | None:
    """容错解析：个别页面语法太歪，失败就退回 None（调用方走正则兜底）。"""
    try:
        return mwparserfromhell.parse(text)
    except Exception:
        return None


# ---------------------------------------------------------------- 纯文本化
def _plain(node: Any, depth: int = 0) -> str:
    """把 mwparserfromhell 节点递归转成纯文本。"""
    if depth > 12:
        return ""
    name = type(node).__name__

    if name == "Text":
        return str(node)

    if name == "Comment":
        return ""

    if name == "Wikilink":
        title = str(node.title).strip()
        low = title.lower()
        if low.startswith(_CAT_PREFIXES) or low.startswith(_FILE_PREFIXES):
            return ""
        label = node.text
        if label is not None:
            return _plain(label, depth + 1)
        return title

    if name == "ExternalLink":
        label = node.text if getattr(node, "text", None) is not None else None
        if label is not None:
            return _plain(label, depth + 1)
        return str(node.url)

    if name == "Tag":
        tag = str(node.tag).strip().lower()
        if tag in _DROP_TAGS:
            return ""
        if tag in ("br", "BR"):
            return "\n"
        if tag in _TRANSPARENT_TAGS:
            return _plain(node.contents, depth + 1)
        if tag in _UNWRAP_TAGS:
            return _plain(node.contents, depth + 1)
        # 其余未知标签：保留内容
        return _plain(node.contents, depth + 1)

    if name == "Template":
        return _plain_template(node, depth)

    if name == "Heading":
        return f"\n\n{'=' * node.level} {_plain(node.title, depth + 1).strip()} {'=' * node.level}\n\n"

    if name == "Argument":
        return ""

    if name == "HTMLEntity":
        try:
            return html.unescape(str(node))
        except Exception:
            return ""

    # Wikicode / 其它容器
    contents = getattr(node, "nodes", None)
    if contents is None:
        return str(node)
    return "".join(_plain(c, depth + 1) for c in contents)


def _plain_template(tpl: Any, depth: int) -> str:
    name = str(tpl.name).strip()
    name = re.sub(r"^模板:", "", name)
    params = {str(p.name).strip(): p.value for p in tpl.params}

    if name in _VALUE_TEMPLATES:
        if name in ("强化值", "营养价值"):
            parts = [f"{k}{_plain(v, depth + 1).strip()}" for k, v in params.items()]
            return " ".join(p for p in parts if p)
        if name in ("小图标", "小图标2", "小图标3", "小图标2R", "小图标2不推荐",
                    "小图标2无文字", "装备名链接", "装备大图标", "装备外观", "技能图标"):
            return _plain(params.get("1", ""), depth + 1).strip()
        if name in ("划掉重说", "黑幕", "文案引号"):
            return _plain(params.get("1", ""), depth + 1).strip()
        if name in ("颜色", "转化颜色", "变色"):
            for key in ("1", "2", "3"):
                if key in params:
                    return _plain(params[key], depth + 1).strip()
            return ""
        if name == "模板":
            return _plain(params.get("1", ""), depth + 1).strip()

    # 通用兜底：展示型模板第一个无名参数通常就是正文
    unnamed = _plain(params.get("1", ""), depth + 1).strip()
    if unnamed:
        return unnamed
    return ""


def to_plain(text: str) -> str:
    """整段 wikitext → 纯文本（标题保留为 == x ==，方便分节）。"""
    wc = parse_wikitext(text)
    if wc is None:
        return _regex_plain(text)
    out = _plain(wc)
    out = re.sub(r"'''?", "", out)          # 粗体/斜体标记
    out = re.sub(r"__[A-Z]+__", "", out)     # __TOC__ / __NOTOC__
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _regex_plain(text: str) -> str:
    """mwparserfromhell 失败时的粗暴兜底。"""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.S | re.I)
    t = re.sub(r"<ref[^>]*/>", "", t, flags=re.I)
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", t)
    t = re.sub(r"\[\[[^\]]*\]\]", "", t)
    t = re.sub(r"'''?", "", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# ---------------------------------------------------------------- 表格
def tables_to_lines(text: str) -> str:
    """把残留的 wikitable 语法压成 "单元格 | 单元格" 的可读行。"""
    out: list[str] = []
    row: list[str] = []
    depth = 0

    def flush() -> None:
        if not row:
            return
        cells = [c.strip() for c in row]
        line = " | ".join(c for c in cells if c)
        if line:
            out.append(line)
        row.clear()

    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("{|"):
            depth += 1
            continue
        if s.startswith("|}"):
            depth = max(0, depth - 1)
            flush()
            continue
        if depth <= 0:
            out.append(raw)
            continue
        if s.startswith("|-"):
            flush()
            continue
        body = s
        if body[:1] in ("|", "!"):
            body = body[1:]
        parts = re.split(r"\|\||!!", body)
        if row and parts:
            row[-1] = (row[-1] + " " + parts[0].strip()).strip()
            row.extend(p.strip() for p in parts[1:])
        else:
            row.extend(p.strip() for p in parts)
    flush()
    return "\n".join(out)


# ---------------------------------------------------------------- infobox
def find_templates(wc: Any, names: Iterable[str]) -> list[Any]:
    want = set(names)
    return [t for t in wc.filter_templates(recursive=True) if str(t.name).strip() in want]


def is_nav_template(name: str) -> bool:
    n = name.strip()
    return any(n == p or n.startswith(p) for p in NAV_TEMPLATE_PREFIXES)


def infobox_fields(tpl: Any) -> dict[str, str]:
    """infobox 模板参数 → {字段名: 纯文本值}（丢掉空值和注释）。"""
    fields: dict[str, str] = {}
    for p in tpl.params:
        key = str(p.name).strip()
        if not key or key.isdigit():
            continue
        val = _plain(p.value).strip()
        val = re.sub(r"[ \t]+", " ", val)
        val = re.sub(r"\n{3,}", "\n\n", val).strip()
        if val:
            fields[key] = val
    return fields


def top_level_infobox(wikitext: str, names: Iterable[str]) -> tuple[Any, str] | None:
    """在正文里找 infobox 模板，返回 (模板节点, 去掉它之后的正文 wikitext)。"""
    wc = parse_wikitext(wikitext)
    if wc is None:
        return None
    want = set(names)
    for tpl in wc.filter_templates(recursive=False):
        if str(tpl.name).strip() in want:
            return tpl, wikitext.replace(str(tpl), "", 1)
    return None
