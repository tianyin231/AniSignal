"""结构化抽取器：infobox 字段 → SQLite 行 + 卡片摘要文本。

两条路，driver（parse.py）按 kind 先查游戏配置的 extractor_module，查不到再走通用实体路：

  * **专用抽取器**（azurlane.py）：碧蓝航线的舰娘/装备/关卡图鉴…。字段语义和表结构
    （ship_stats / skills / ship_slots）是这个游戏特有的，写死在代码里更清楚。
    由 games/azurlane.yaml 的 `extractor_module: kblib.extractors.azurlane` 挂上。
  * **通用实体路**（本文件的 generic_entity）：完全由 games/*.yaml 的 `parse.kinds`
    驱动——卡片字段、attr 字段、子模板列名→字段名映射、编号字段拆行，全在配置里。
    明日方舟（CharinfoV2/敌人信息/道具信息/关卡信息…）零代码进结构化路。

每款游戏只挂自己的 extractor_module，kind 名撞车也不会串味。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .. import cleantext
from ..gamecfg import GameConfig


@dataclass
class Row:
    """一张表的一行。driver 只负责 INSERT OR REPLACE，不关心是哪张表。"""

    table: str
    values: dict[str, Any]


@dataclass
class ExtractResult:
    rows: list[Row] = field(default_factory=list)
    card_text: str | None = None


@dataclass
class ExtractCtx:
    """一页的上下文，抽取器能拿到的全部东西。"""

    title: str
    page_id: int | None
    kind: str
    category: str | None
    fields: dict[str, str]
    cfg: GameConfig
    wikitext: str
    # 剥掉 infobox 之后的正文 wikitext（子模板在原文里找，infobox 本身已排除）
    body_wikitext: str
    # 顶层模板节点（整页解析一次，多处复用，别反复 parse）
    top_level_templates: list[Any] = field(default_factory=list)


Extractor = Callable[[ExtractCtx], ExtractResult]


# ---------------------------------------------------------------- 通用实体路
def _resolve(fields: dict[str, str], alias: str, groups: Sequence[str],
             index: int, key: str) -> str | None:
    """字段别名 → 值。支持三个特殊来源：

      @key   当前匹配到的字段名本身（模式拆行时用来取"值就是这个字段"）
      @index 模板第几次出现（occurrence 行）
      {0}    正则捕获组，`技能{0}初始` → `技能1初始`；整个别名就一个 `{N}` 时
             直接返回该组本身（YAML 里 `stage: '{0}'` 写的就是这个意思）
    """
    alias = str(alias)
    if alias == "@key":
        return fields.get(key)
    if alias == "@index":
        return str(index)
    if "{" in alias:
        # 别名整个就是 `{N}`：值 = 第 N 个捕获组，别 format 完再去 fields 里
        # 找一个叫 "0" 的字段（那只会拿到 None，且不报错，极难发现）
        only_group = re.fullmatch(r"\{(\d+)\}", alias)
        if only_group:
            gi = int(only_group.group(1))
            return groups[gi] if gi < len(groups) else None
        try:
            alias = alias.format(*groups)
        except (IndexError, KeyError):
            return None
    return fields.get(alias)


def _first(fields: dict[str, str], aliases: Iterable[str], groups: Sequence[str] = (),
           index: int = 0, key: str = "") -> str | None:
    for a in aliases:
        v = _resolve(fields, a, groups, index, key)
        if v:
            return v
    return None


def generic_card(title: str, fields: dict[str, str], spec: dict) -> str | None:
    """按配置里的 card_fields 渲染卡片摘要（干员职业/分支、敌人种类/能力…）。"""
    bits: list[str] = []
    name = None
    for nf in spec.get("name_fields", []) or []:
        name = fields.get(nf)
        if name:
            break
    bits.append(f"{name or title}（{title}）")
    for entry in spec.get("card_fields", []) or []:
        if isinstance(entry, dict):
            label, keys = next(iter(entry.items()))
            keys = keys if isinstance(keys, list) else [keys]
        else:
            label, keys = entry[0], [entry[1]]
        v = _first(fields, keys)
        if v:
            bits.append(f"{label}{v}")
    text = "。".join(b.strip() for b in bits if b and b.strip())
    return (text + "。") if text else None


def _pattern_rows(ctx: ExtractCtx, template: str, index: int,
                  fields: dict[str, str], spec: dict, default_table: str = "") -> list[Row]:
    r"""按正则把编号字段拆成多行：`技能(\d+)描述` → 每级一行。

    表名默认继承所属子模板的 table（YAML 里不用在每个 rows_from 下重复写）。
    `block` 记这是所属子模板的第几次出现（干员三个技能 = 1/2/3，敌人两级 = 1/2）：
    同一页多个块里 `技能7初始` 会撞名，只按 sort 排序时先出哪行全看 rowid，
    查表探针必须能按块区分。
    """
    try:
        rx = re.compile(str(spec["pattern"]))
    except re.error:
        return []
    columns: dict[str, Any] = spec.get("columns", {}) or {}
    table = str(spec.get("table") or default_table or "")
    if not table or not columns:
        return []
    const = dict(spec.get("const", {}) or {})
    rows: list[Row] = []
    for sort, (key, _v) in enumerate(fields.items(), start=1):
        m = rx.match(key)
        if not m:
            continue
        groups = [g or "" for g in m.groups()]
        vals: dict[str, Any] = {"page_title": ctx.title, "sort": sort,
                                "template": template, "block": index}
        vals.update(const)
        for col, aliases in columns.items():
            aliases = aliases if isinstance(aliases, list) else [aliases]
            vals[col] = _first(fields, aliases, groups, index, key)
        rows.append(Row(table, vals))
    return rows


def _child_rows(ctx: ExtractCtx, spec: dict) -> list[Row]:
    """子模板（技能2/属性/敌人信息/levelcontent…）→ 行。

    `template` 给单个名字或一组名字：同一结构在 wiki 上换过名字（PRTS 的
    `技能2` 和老版 `技能` 参数一模一样，混在不同干员页上）时写一组，抽出来的行
    落同一张表，`template` 列记下实际命中的那个。

    occurrence: true 表示一页会出现多次（每个技能一块、每级敌人一块、第几天赋
    各一块），按出现顺序编号；否则只取第一次出现（属性/CharinfoV2 这类一页一块的）。

    recursive: true 表示连嵌在别的模板里的也要找。星铁 BWIKI 把技能详情整个塞进
    infobox 的字段值里：`|普攻={{行迹/技能|类型=普攻|名称=呀，漏网之鱼|描述=…}}`，
    顶层模板池里根本没有 行迹/技能 这个节点——不递归的话 36 个角色的技能名和描述
    在结构化路和正文路两头一起丢（_plain 对全命名参数的模板兜底返回空串）。
    """
    names = spec.get("template") or []
    if isinstance(names, str):
        names = [names]
    names = [str(n).strip() for n in names if str(n).strip()]
    if not names:
        return []
    want = set(names)
    if spec.get("recursive"):
        wc = cleantext.parse_wikitext(ctx.wikitext)
        pool = list(wc.filter_templates(recursive=True)) if wc else []
    else:
        pool = ctx.top_level_templates
    nodes = [t for t in pool if cleantext.template_name(t) in want]
    if not nodes:
        return []
    if not spec.get("occurrence"):
        nodes = nodes[:1]
    rows: list[Row] = []
    for idx, node in enumerate(nodes, start=1):
        fields = cleantext.infobox_fields(node)
        if not fields:
            continue
        table = str(spec.get("table", "") or "")
        columns = spec.get("columns", {}) or {}
        if table and columns:
            vals: dict[str, Any] = {"page_title": ctx.title, "sort": idx,
                                    "template": cleantext.template_name(node)}
            for col, aliases in columns.items():
                aliases = aliases if isinstance(aliases, list) else [aliases]
                vals[col] = _first(fields, aliases, (), idx, "")
            rows.append(Row(table, vals))
        for rf in spec.get("rows_from", []) or []:
            rows.extend(_pattern_rows(ctx, cleantext.template_name(node), idx, fields, rf, table))
    return rows


def generic_entity(ctx: ExtractCtx) -> ExtractResult:
    """通用实体路：CharinfoV2/敌人信息/道具信息 这类"字段即结构"的模板都走这里。"""
    spec = ctx.cfg.kinds.get(ctx.kind, {}) or {}
    name = None
    for nf in spec.get("name_fields", []) or []:
        name = ctx.fields.get(nf)
        if name:
            break
    rows = [Row("entities", {
        "page_id": ctx.page_id, "title": ctx.title, "kind": ctx.kind,
        "category": ctx.category, "name": name,
        "fields_json": json.dumps(ctx.fields, ensure_ascii=False),
    })]
    for attr in spec.get("attr_fields", []) or []:
        v = ctx.fields.get(attr)
        if v:
            rows.append(Row("entity_attrs", {
                "title": ctx.title, "kind": ctx.kind, "attr": attr, "value": v,
            }))
    for spec_tpl in spec.get("child_templates", []) or []:
        rows.extend(_child_rows(ctx, spec_tpl))
    return ExtractResult(rows=rows, card_text=generic_card(ctx.title, ctx.fields, spec))


# ---------------------------------------------------------------- 表结构
# 所有游戏共用的骨架表
CORE_SCHEMA = """
CREATE TABLE pages(
  page_id INTEGER PRIMARY KEY,
  title TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  url TEXT NOT NULL,
  wikitext_len INTEGER,
  infobox_template TEXT,
  infobox_fields INTEGER DEFAULT 0,
  infobox_ok INTEGER DEFAULT 0
);
CREATE INDEX idx_pages_kind ON pages(kind);

-- 重定向别名（内容在目标页里，但别名本身也要能查到）
CREATE TABLE aliases(
  alias TEXT PRIMARY KEY, target TEXT NOT NULL
);
CREATE INDEX idx_aliases_target ON aliases(target);

-- 图片 URL（只记链接不下载）。file 是 wiki 文件名，url 由 imageinfo 批量换，
-- 没换到就留 NULL（fetch_images.py 补跑后可再 parse 一次补齐）
CREATE TABLE page_images(
  title TEXT NOT NULL, sort INTEGER NOT NULL, file TEXT NOT NULL, url TEXT,
  PRIMARY KEY(title, sort)
);

CREATE TABLE parse_stats(
  key TEXT PRIMARY KEY, value TEXT
);
"""

# 通用实体路：主 infobox 一行 + 扁平字段一行（kind 区分游戏内实体类型）。
# 只在该游戏真用通用实体路时建——碧蓝航线全走专用抽取器，schema 和 v3 一模一样。
GENERIC_SCHEMA = """
CREATE TABLE entities(
  page_id INTEGER PRIMARY KEY,
  title TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  category TEXT,
  name TEXT,
  fields_json TEXT
);
CREATE INDEX idx_entities_kind ON entities(kind);
CREATE INDEX idx_entities_name ON entities(name);

CREATE TABLE entity_attrs(
  title TEXT NOT NULL, kind TEXT NOT NULL, attr TEXT NOT NULL, value TEXT,
  PRIMARY KEY(title, kind, attr)
);
CREATE INDEX idx_entity_attrs_kind ON entity_attrs(kind, attr);
"""

# 通用实体路子表共用的列（各游戏在 YAML 里只声明自己的业务列）
CHILD_TABLE_COLUMNS = "page_title TEXT NOT NULL, sort INTEGER, template TEXT"


def child_table_columns(cfg: GameConfig) -> dict[str, list[str]]:
    """按配置里的 child_templates 声明归并列：{表名: [列名...]}（保序去重）。

    一张表可能同时被"主行"和"编号拆行"两种声明写（比如技能主表 + 技能等级表各一张），
    这里按表名归并列。eval 的查表探针也靠它做表名/列名白名单。

    `block` 是编号拆行（rows_from）额外带的列——所属子模板的第几次出现，由
    _pattern_rows 写，所以在这儿补进 DDL 和探针白名单。
    """
    tables: dict[str, list[str]] = {}

    def add(table: str, columns) -> None:
        if not table:
            return
        cols = tables.setdefault(table, [])
        for col in columns or {}:
            if col not in cols:
                cols.append(col)

    for spec in cfg.kinds.values():
        for tpl in spec.get("child_templates", []) or []:
            table = str(tpl.get("table", "") or "")
            add(table, tpl.get("columns", {}) or {})
            # rows_from 不写 table 时继承所属子模板的表（和 _pattern_rows 一致）
            for rf in tpl.get("rows_from", []) or []:
                rf_table = str(rf.get("table", "") or table)
                add(rf_table, rf.get("columns", {}) or {})
                add(rf_table, {"block": None})
    return tables


def child_table_ddl(cfg: GameConfig) -> str:
    """按配置里的 child_templates 声明生成子表 DDL（列名去重、保序）。"""
    ddl = []
    for table, cols in child_table_columns(cfg).items():
        col_ddl = ", ".join(f'"{c}" TEXT' for c in cols)
        ddl.append(f"CREATE TABLE {table}(\n  {CHILD_TABLE_COLUMNS}"
                   + (f",\n  {col_ddl}" if col_ddl else "")
                   + f"\n);\nCREATE INDEX idx_{table}_title ON {table}(page_title);")
    return "\n".join(ddl)
