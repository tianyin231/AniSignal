"""wikitext → 纯文本 / 结构化字段的清洗工具。

两条路都用它：
  * 结构化路：infobox 模板参数 → dict
  * 文本路：正文 → 去噪 → 表格转行 → 分节分段
"""

from __future__ import annotations

import html
import re
from contextlib import contextmanager
from typing import Any, Iterable

import mwparserfromhell

from .config import GAMES_DIR  # noqa: F401  兼容老 import 路径
from .gamecfg import DEFAULT_GAME, load_game

# ---- 常量 ----
_CAT_PREFIXES = ("分类:", "category:", "Category:")
_FILE_PREFIXES = ("file:", "image:", "文件:", "檔案:", "档案:")

# 整个标签连内容一起丢掉的 HTML 标签
# bstyle 是 BWIKI 私有的样式块（<bstyle>.name-section{…}</bstyle>），不在标准
# HTML 里，不显式丢掉会按"未知标签保留内容"把一整段 CSS 漏进正文。
_DROP_TAGS = {
    "ref", "references", "nowiki", "gallery", "timeline", "syntaxhighlight",
    "source", "math", "code", "pre", "imagemap", "inputbox", "quiz", "bstyle",
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

# ---------------------------------------------------------------- 展示型模板
# 每个 wiki 都有一批"参数即展示"的模板，但参数顺序是给机器用的：
#   PRTS  {{color|#0098DC|+30}}    第一个参数是色值，给人看的是第二个
#         {{术语|ba.stun|晕眩}}    第一个是词条 key
#         {{+|70|+70}}             第一个是新值，第二个是带符号的增量
#         {{mdi|alpha-x-box-outline}}  图标名，压根不该出现在正文里
# 通用兜底（取无名参数 1）在这种模板上只会掏出机器值。游戏在 YAML 的
# `parse.display_templates` 里声明"渲染哪几个参数"，_plain 就按声明来；
# 不声明 = 不扩展，碧蓝航线的输出一个字节都不变。
#
# 这是进程级单例：parse.py 一进程只炼一款游戏，进 run() 时设一次。
# 别在多线程里改它——真要并发就改成 ContextVar。
_PLAIN_DISPLAY: dict[str, tuple[str, ...]] = {}
_STRIP_NAME_COMMENTS = False


@contextmanager
def plain_display(mapping: dict[str, tuple[str, ...]] | None):
    """进段落文本时按游戏配置切换展示型模板渲染表。"""
    global _PLAIN_DISPLAY
    old = _PLAIN_DISPLAY
    _PLAIN_DISPLAY = dict(mapping or {})
    try:
        yield
    finally:
        _PLAIN_DISPLAY = old


@contextmanager
def template_name_comments(strip: bool):
    """模板名里嵌 HTML 注释时是否剥掉再比对（见 template_name）。"""
    global _STRIP_NAME_COMMENTS
    old = _STRIP_NAME_COMMENTS
    _STRIP_NAME_COMMENTS = bool(strip)
    try:
        yield
    finally:
        _STRIP_NAME_COMMENTS = old


@contextmanager
def wiki_style(display: dict[str, tuple[str, ...]] | None = None,
               strip_name_comments: bool = False):
    """一次设好这款 wiki 的段落文本风格（parse.py 进 run() 时调）。"""
    with plain_display(display), template_name_comments(strip_name_comments):
        yield


def parse_wikitext(text: str) -> mwparserfromhell.wikicode.Wikicode | None:
    """容错解析：个别页面语法太歪，失败就退回 None（调用方走正则兜底）。"""
    try:
        return mwparserfromhell.parse(text)
    except Exception:
        return None


# ---------------------------------------------------------------- 模板名归一化
_NAME_COMMENT = re.compile(r"<!--.*?-->", re.S)


def template_name(tpl: Any, strip_comments: bool | None = None) -> str:
    """模板节点 → 归一化名字：去首尾空白、去"模板:"前缀。

    strip_comments 不给就用进程级开关（wiki_style 设的那份）。PRTS 的干员页把
    模板写成了 `{{CharinfoV2\\n<!--下方为自动更新部分-->|职业=…}}`，注释整个落在
    模板名里，`str(t.name).strip()` 匹配不上任何白名单——开这个开关才剥得掉。
    碧蓝航线不开：它那边唯一的样例是"地图需要模板"这张模板定义页，剥了会从
    other 变成关卡图鉴，v2/v3 那批向量就白对齐了。
    """
    if strip_comments is None:
        strip_comments = _STRIP_NAME_COMMENTS
    n = str(tpl.name)
    if strip_comments:
        n = _NAME_COMMENT.sub("", n)
    return re.sub(r"^模板:", "", n.strip()).strip()


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
    name = template_name(tpl)
    params = {str(p.name).strip(): p.value for p in tpl.params}

    # 解析器函数把正文塞进了"模板名"里。`{{#replace:第18次维检：…}}` 冒号后紧跟
    # 非空白、中间没有 |，mwparserfromhell 就把整段都当成模板名（name ==
    # "#replace:第18次维检：…"），通用兜底只取参数 1 → 整段内容凭空消失。鸣潮
    # BWIKI 的 道具/* 页（日记/记录日志/书籍/生态调查）28 处 9000 字全是这个写法。
    # 规则：名字以 # 开头、冒号后那段够长（>=12，排除 {{#css:x}} {{#var:a}} 这种
    # 真解析器函数的短参数）且不含模板语法（排除 {{#if:{{{参数名|}}}}})就当正文渲染。
    # 别的游戏没有这种写法，输出一个字节不变。
    if name.startswith("#") and ":" in name:
        tail = _plain(tpl.name, depth + 1).split(":", 1)[1].strip()
        if len(tail) >= 12 and "{{" not in tail:
            # 编辑在 #replace 里写字时用字面量 \n 换行，还原成真换行再进正文
            return tail.replace("\\n", "\n")

    # 游戏配置声明的展示型模板（PRTS 的 color/术语/+/*…），优先于 _VALUE_TEMPLATES。
    # 必须先查配置：星铁 BWIKI 的 {{颜色|描述2|50%/100%}} 和 {{颜色|冰|冰属性}} 给人看的
    # 值都在位置参数 2（参数 1 是机器键），而下面的 _VALUE_TEMPLATES 兜底取参数 1，
    # 会把正文渲染成"描述2攻击力的冰伤害"。没声明这个模板名的游戏不受影响——
    # _PLAIN_DISPLAY 查不到就原样落到 _VALUE_TEMPLATES，碧蓝航线输出一个字节不变。
    spec = _PLAIN_DISPLAY.get(name)
    if spec is not None:
        parts = [_plain(params.get(p, ""), depth + 1).strip() for p in spec]
        return " ".join(p for p in parts if p)

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
    # 解析失败的解析器函数残骸：`{{#css: …}}`/`{{#ask: …}}` 冒号后带空格时
    # mwparserfromhell 不认它是模板，整块以 Text 原样漏进正文（鸣潮 105 页
    # 4 万字符，最坏的是 共鸣者/武器/敌人 这些索引页）。纯噪音，整块删掉。
    # (?:(?!\{\{).)*? 是不跨越下一个 {{ 的非贪婪匹配——万一某块没写全闭合，
    # 也不会把后面一整页的正文一起吞掉。
    out = re.sub(r"\{\{#(?:(?!\{\{).)*?\}\}", "", out, flags=re.S)
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
    return [t for t in wc.filter_templates(recursive=True) if template_name(t) in want]


def is_nav_template(name: str, prefixes=None) -> bool:
    """导航/样板模板判定。prefixes 不给就用默认游戏的配置。"""
    if prefixes is None:
        prefixes = load_game(DEFAULT_GAME).nav_template_prefixes
    n = name.strip()
    return any(n == p or n.startswith(p) for p in prefixes)


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


def find_top_level_infobox(src: Any, names: Iterable[str],
                           legacy_names: Iterable[str] = (),
                           prefixes: Iterable[str] = ()) -> Any | None:
    """在顶层模板里找 infobox，返回模板节点（不给正文，省一次字符串替换）。

    三段匹配，顺序即优先级：老家族（legacy_names）→ 白名单全量（names）→
    前缀家族（塞壬图鉴/*）。先老后新是为了让 azurlane v3 的 chunk_id 和 v2 完全
    对齐——见 games/azurlane.yaml 的 legacy_infobox_template_names 注释。

    src 可以是 wikitext 字符串或已解析的 Wikicode（parse.py 一页只解析一次，
    这里复用，别反复 parse）。
    """
    wc = src if not isinstance(src, str) else parse_wikitext(src)
    if wc is None:
        return None
    templates = list(wc.filter_templates(recursive=False))
    for want in (set(legacy_names), set(names)):
        for tpl in templates:
            if template_name(tpl) in want:
                return tpl
    for tpl in templates:
        name = template_name(tpl)
        for prefix in prefixes:
            if name == prefix or name.startswith(prefix + "/"):
                return tpl
    return None


def top_level_infobox(wikitext: str, names: Iterable[str],
                      legacy_names: Iterable[str] = (),
                      prefixes: Iterable[str] = ()) -> tuple[Any, str] | None:
    """(模板节点, 去掉它之后的正文 wikitext)。薄封装，工具脚本用。"""
    tpl = find_top_level_infobox(wikitext, names, legacy_names, prefixes)
    if tpl is None:
        return None
    return tpl, wikitext.replace(str(tpl), "", 1)


def infobox_template_name(tpl: Any, prefixes: Iterable[str] = ()) -> str:
    """模板节点 → 归一化后的模板名（塞壬图鉴家族的子页名统一成家族名）。"""
    name = template_name(tpl)
    for prefix in prefixes:
        if name == prefix or name.startswith(prefix + "/"):
            return prefix
    return name


# ---------------------------------------------------------------- v3 补捞
# 下面几个函数都只做"增量补捞"：从 wikitext 里把 _plain 主动丢掉或只取了
# 第一个参数的内容另存成独立 chunk，绝不改动主正文路径——现有 prose/card
# chunk 的 chunk_id 必须一个都不变，增量 upsert 才不会退化成全量重建。

FILE_REF_RE = re.compile(
    r"\[\[\s*(?:文件|檔案|档案|File|Image)\s*:\s*([^\]|#\n]+?)\s*(?:\|[^\]]*)?\]\]", re.I
)
GALLERY_RE = re.compile(r"<\s*gallery([^>]*)>(.*?)<\s*/\s*gallery\s*>", re.I | re.S)
COMMENT_RE = re.compile(r"<!--(.*?)-->", re.S)
# 注释里混进来的 base64 音频/图片 blob：捞它只会得到一坨乱码
BLOB_RE = re.compile(r"base64\s*,|[A-Za-z0-9+/]{200,}")
COMMENT_MIN = 80          # 短于这个长度的注释基本是编辑备注，CHUNK_MIN 也会拦
COMMENT_MAX = 20000       # 超长注释视为数据 dump，不捞


def extract_image_files(wikitext: str, limit: int | None = None) -> list[str]:
    """正文里引用的文件名（去重、保序）。只记名字，URL 由 imageinfo 批量换。"""
    if limit is None:
        limit = load_game(DEFAULT_GAME).max_images_per_page
    seen: set[str] = set()
    out: list[str] = []
    for m in FILE_REF_RE.finditer(wikitext):
        name = m.group(1).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= limit:
            break
    return out


def gallery_lines(wikitext: str) -> list[str]:
    """<gallery> 图库 → 可读行。

    原来是整块丢（_DROP_TAGS 里有 gallery），图注（画师名、日期、装备名）
    跟着一起没了。307 篇的图注超过 300 字，最大的"画师贺图、同人图合集"
    一篇 77KB。
    """
    out: list[str] = []
    for m in GALLERY_RE.finditer(wikitext):
        for raw in m.group(2).split("\n"):
            line = raw.strip()
            if not line:
                continue
            parts = re.split(r"\|", line, maxsplit=1)
            fname = parts[0].strip()
            caption = re.sub(r"\s+", " ", parts[1]).strip() if len(parts) > 1 else ""
            if not fname:
                continue
            out.append(f"{fname} {caption}".strip() if caption else fname)
    return out


def hidden_lines(wikitext: str) -> list[str]:
    """HTML 注释里的正文 → 可读行。

    编辑常用 <!-- --> 藏旧版攻略/备份数据，98 篇的注释正文超过 800 字，
    最大的"艦船少女セリフ集"65KB。注释按 MediaWiki 语义确实不渲染，但内容
    是 wiki 上真实存在的知识，按"宁可多炼不可漏炼"捞出来单列一类。
    """
    out: list[str] = []
    for m in COMMENT_RE.finditer(wikitext):
        body = m.group(1).strip()
        if len(body) < COMMENT_MIN or len(body) > COMMENT_MAX:
            continue
        if BLOB_RE.search(body):
            continue
        text = to_plain(body)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{2,}", "\n", text).strip()
        if len(text) >= COMMENT_MIN:
            out.append(text)
    return out


def template_dump(src: Any, exclude: Any = None,
                  nav_prefixes=None) -> list[str]:
    """数据型模板的全参数转存。

    `_plain_template` 的通用兜底只取无名参数 1，像 `{{世界巡游|1=物资|1n=…}}`
    （整页是 100 级奖励表）、`{{每月签到}}`（116KB）、`{{InfoBox}}`、
    `{{META图鉴}}`（介绍字段几千字）这种"参数即正文"的模板就只剩两个字。
    这里把这类模板的所有参数摊平，交给调用方另切 chunk。
    """
    wc = src if not isinstance(src, str) else parse_wikitext(src)
    if wc is None:
        return []
    skip = str(exclude) if exclude is not None else None
    if nav_prefixes is None:
        nav_prefixes = load_game(DEFAULT_GAME).nav_template_prefixes
    out: list[str] = []
    for tpl in wc.filter_templates(recursive=False):
        raw = str(tpl)
        if skip and raw == skip:
            continue
        name = template_name(tpl)
        if name.startswith("#") or is_nav_template(name, nav_prefixes):
            continue
        inline = _plain(tpl).strip()
        total = sum(len(_plain(p.value).strip()) for p in tpl.params)
        # 已经在正文里露出过半内容的模板不用再转存一遍
        if total <= 0 or len(inline) * 2 >= total:
            continue
        parts: list[str] = []
        for p in tpl.params:
            k = str(p.name).strip()
            v = re.sub(r"\s+", " ", _plain(p.value).strip())
            if not v:
                continue
            parts.append(f"{k}={v}" if k and not k.isdigit() else v)
        if len(parts) >= 3:
            out.append(f"{name}: " + " | ".join(parts))
    return out


def voice_lines(fields: dict[str, str], suffix: str | None = None) -> list[tuple[str, str]]:
    """infobox 字段里的台词 → [(场景, 台词)]。

    台词是 `舰娘图鉴`/`同人角色` 的 `*台词` 参数，原来只落 fields_json，
    向量库一个字都检索不到。
    """
    if suffix is None:
        suffix = load_game(DEFAULT_GAME).voice_field_suffix
    out: list[tuple[str, str]] = []
    for k, v in fields.items():
        if not k.endswith(suffix):
            continue
        scene = k[: -len(suffix)].strip() or "台词"
        v = re.sub(r"\s+", " ", v).strip()
        if v:
            out.append((scene, v))
    return out


def long_field_lines(fields: dict[str, str], min_len: int | None = None) -> list[str]:
    """infobox 里的长文本字段 → 可读行。

     infobox 一旦被抽取就会从正文消失，长字段（META图鉴的"介绍"、技能描述、
    审核意见等）原来只进 fields_json。这里把超过阈值的字段摊平补一个 chunk。
    台词字段除外——它们由 voice_lines 单独切成 kind=voice 的 chunk。
    """
    if min_len is None:
        min_len = load_game(DEFAULT_GAME).fields_chunk_min
    suffix = load_game(DEFAULT_GAME).voice_field_suffix
    out: list[str] = []
    for k, v in fields.items():
        if k.endswith(suffix):
            continue
        v = re.sub(r"\s+", " ", v).strip()
        if len(v) >= min_len:
            out.append(f"{k}: {v}")
    return out
