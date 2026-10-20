#!/usr/bin/env python3
"""阶段 2：解析。wikitext → 结构化 SQLite + 语义 chunk JSONL。

  * 结构化路：infobox（舰娘图鉴/舰炮图鉴/舰载机图鉴/…）→ data/kb.db
  * 文本路：正文去噪 → 分节 → 分段（一段讲一件事）→ data/chunks.jsonl

用法：
    ./kb/.venv/bin/python parse.py --source data/raw/spike --db data/spike_kb.db \\
        --chunks data/spike_chunks.jsonl          # spike 验证抽取率
    ./kb/.venv/bin/python parse.py                 # 全量
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

from kblib import cleantext
from kblib.config import (
    CHUNKS_PATH,
    DATA_DIR,
    FIELDS_CHUNK_MIN,
    IMAGE_URLS_PATH,
    INFOBOX_TEMPLATES,
    INFOBOX_TEMPLATE_PREFIXES,
    KB_DB,
    LEGACY_INFOBOX_TEMPLATE_NAMES,
    NOISE_SECTION_KEYWORDS,
    RAW_DIR,
    REDIRECTS_PATH,
    SPIKE_DIR,
    WIKI_BASE,
)

log = logging.getLogger("kb.parse")

# 模板名 → (kind, category)。塞壬图鉴家族的子页名（/仲裁机关、/维序者…）
# 在 top_level_infobox 里已归一成家族名，这里直接查表
TEMPLATE_KINDS: dict[str, tuple[str, str]] = {
    **INFOBOX_TEMPLATES,
    **INFOBOX_TEMPLATE_PREFIXES,
}

# v3 新收入结构化路的图鉴：它们的 card 也走 x 前缀 chunk_id，不占老计数器
NEW_KINDS = frozenset({"stage", "ship_perf", "siren", "fan_ship"})

# 各 kind 的 card 已经把短字段渲染过了，长字段补 chunk 时跳过，别重复炼
CARD_FIELD_KEYS: dict[str, frozenset[str]] = {
    "ship": frozenset({"名称", "和谐名", "型号", "英文名", "日文名", "编号", "类型",
                       "稀有度", "阵营", "初始星级", "装甲类型", "航速", "幸运",
                       "耗时", "评价"}),
    "equipment": frozenset({"名称", "类型", "特性", "稀有度", "所属阵营", "伤害",
                            "标准射速", "备注"}),
}

# ---- chunk 参数 ----
CHUNK_MIN = 60          # 短于此的段落直接丢弃或并入邻段
CHUNK_TARGET = 420      # 合并目标长度
CHUNK_MAX = 900         # 单 chunk 硬上限（超了按句子边界再切）

HEADING_RE = re.compile(r"^(={1,6})\s*(.+?)\s*\1$")
SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])")
STAGE_RE = re.compile(r"^\d+-\d+(Hard)?$|^\d+-SOS$|^SP\d*$|^EX\d*$|^[A-Z]{2}\d*$")

NOISE_PARAGRAPH_RES = [
    re.compile(r"^最后编辑"),
    re.compile(r"^本页面最后"),
    re.compile(r"^本文引用"),
    re.compile(r"^参考资料"),
    re.compile(r"^版权"),
    re.compile(r"^\s*[\d\s\|\-:：,，.。]*$"),
]

SHIP_STATS = [
    "耐久", "炮击", "雷击", "航空", "装填", "命中", "机动", "防空", "反潜", "消耗",
]


# ---------------------------------------------------------------- 数据库
SCHEMA = """
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

CREATE TABLE ships(
  page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
  title TEXT NOT NULL UNIQUE,
  name TEXT, model TEXT, name_en TEXT, name_jp TEXT, code TEXT,
  ship_type TEXT, rarity TEXT, faction TEXT, stars TEXT, hull_type TEXT,
  speed REAL, luck INTEGER, build_time TEXT, acquisition TEXT,
  rating TEXT, notes TEXT, cv TEXT, painter TEXT, fields_json TEXT
);
CREATE INDEX idx_ships_name ON ships(name);
CREATE INDEX idx_ships_faction ON ships(faction);
CREATE INDEX idx_ships_type ON ships(ship_type);

CREATE TABLE ship_stats(
  title TEXT NOT NULL, stat TEXT NOT NULL,
  initial_value TEXT, max_value TEXT,
  PRIMARY KEY(title, stat)
);

CREATE TABLE skills(
  ship_title TEXT NOT NULL, slot INTEGER NOT NULL, variant TEXT NOT NULL DEFAULT '',
  name TEXT, description TEXT,
  PRIMARY KEY(ship_title, slot, variant)
);
CREATE INDEX idx_skills_name ON skills(name);

CREATE TABLE ship_slots(
  ship_title TEXT NOT NULL, slot INTEGER NOT NULL,
  equip_name TEXT, equip_type TEXT, eff_initial TEXT, eff_max TEXT,
  PRIMARY KEY(ship_title, slot)
);

CREATE TABLE equipments(
  page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
  title TEXT NOT NULL UNIQUE,
  name TEXT, category TEXT, equip_type TEXT, trait TEXT,
  rarity TEXT, faction TEXT, damage TEXT, rof TEXT, notes TEXT, fields_json TEXT
);
CREATE INDEX idx_equip_name ON equipments(name);
CREATE INDEX idx_equip_type ON equipments(equip_type);

CREATE TABLE cats(
  page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
  title TEXT NOT NULL UNIQUE,
  name TEXT, rarity TEXT, fields_json TEXT
);

-- v3 新增：原来这四类图鉴模板一个都没进结构化路
CREATE TABLE stages(
  page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
  title TEXT NOT NULL UNIQUE,
  map_no TEXT, map_name TEXT, intro TEXT,
  clear_reward TEXT, three_star_cond TEXT, three_star_reward TEXT,
  req_air TEXT, enemy_air TEXT, rec_air TEXT,
  weekly_enemy_air TEXT, weekly_rec_air TEXT, ship_limit TEXT,
  enemy_level TEXT, boss_level TEXT, boss_pos TEXT, boss_lineup TEXT,
  req_cmd_level TEXT, danger TEXT,
  oil_melee TEXT, oil_flagship TEXT, oil_sub TEXT, sub_start TEXT, drops TEXT,
  fields_json TEXT
);
CREATE INDEX idx_stages_map ON stages(map_no);

CREATE TABLE ship_perf(
  page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
  title TEXT NOT NULL UNIQUE,
  name TEXT, perf_type TEXT,
  displacement_std TEXT, displacement_full TEXT, length TEXT, beam TEXT,
  draft TEXT, power TEXT, speed TEXT, range TEXT, range_speed TEXT,
  crew TEXT, armament TEXT, armor TEXT, aircraft TEXT, other TEXT,
  fields_json TEXT
);

CREATE TABLE sirens(
  page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
  title TEXT NOT NULL UNIQUE,
  name TEXT, name_en TEXT, faction TEXT, category TEXT, tarot TEXT,
  ship_type TEXT, unit_type TEXT, code TEXT, belongs TEXT,
  stages TEXT, special_power TEXT, intro TEXT, fields_json TEXT
);

CREATE TABLE fan_ships(
  page_id INTEGER PRIMARY KEY REFERENCES pages(page_id),
  title TEXT NOT NULL UNIQUE,
  name TEXT, code TEXT, ship_type TEXT, faction TEXT, author TEXT,
  identity TEXT, personality TEXT, keywords TEXT, fields_json TEXT
);

-- 重定向别名（wiki 主命名空间 759 个）。内容在目标页里，但别名本身
-- 以前查不到——"企业"这种叫法在表里搜不到"企业号"
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


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    # parse 是全量重建：先清掉旧表，保证可重复跑
    old = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in old:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- 结构化抽取
SKILL_RE = re.compile(r"^技能(\d+)(名)?(.*)$")
SLOT_EFF_RE = re.compile(r"^(\d+)号槽装备效率(初始|满破)$")
SLOT_TYPE_RE = re.compile(r"^(\d+)号槽装备类型$")
SLOT_EQUIP_RE = re.compile(r"^装备(\d+)$")


def _num(v: str | None) -> float | None:
    if not v:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
    return float(m.group()) if m else None


def extract_ship(fields: dict[str, str]) -> tuple[dict, list, list, list]:
    """舰娘图鉴字段 → (ships 行, ship_stats 行, skills 行, ship_slots 行)。"""
    stats: dict[str, dict[str, str]] = {}
    for k, v in fields.items():
        m = re.match(r"^初始(.+)$", k)
        if m and m.group(1) in SHIP_STATS:
            stats.setdefault(m.group(1), {})["initial"] = v
        m = re.match(r"^满级(.+)$", k)
        if m and m.group(1) in SHIP_STATS:
            stats.setdefault(m.group(1), {})["max"] = v

    ship = {
        "name": fields.get("名称") or fields.get("和谐名"),
        "model": fields.get("型号"),
        "name_en": fields.get("英文名"),
        "name_jp": fields.get("日文名"),
        "code": fields.get("编号"),
        "ship_type": fields.get("类型"),
        "rarity": fields.get("稀有度"),
        "faction": fields.get("阵营"),
        "stars": fields.get("初始星级"),
        "hull_type": fields.get("装甲类型"),
        "speed": _num(fields.get("航速")),
        "luck": int(_num(fields.get("幸运")) or 0) or None,
        "build_time": fields.get("耗时"),
        "acquisition": fields.get("其他获取途径"),
        "rating": fields.get("评价"),
        "notes": fields.get("备注"),
        "cv": fields.get("CV"),
        "painter": fields.get("画师"),
    }

    skills: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for k, v in fields.items():
        m = SKILL_RE.match(k)
        if not m:
            continue
        slot, is_name, variant = int(m.group(1)), bool(m.group(2)), m.group(3).strip()
        key = (slot, variant)
        if key in seen:
            continue
        seen.add(key)
        name = fields.get(f"技能{slot}名{variant}", "").strip()
        desc = fields.get(f"技能{slot}{variant}", "").strip()
        if not name and not desc:
            continue
        skills.append(
            {"ship_title": None, "slot": slot, "variant": variant,
             "name": name or None, "description": desc or None}
        )

    slots: dict[int, dict[str, str]] = {}
    for k, v in fields.items():
        m = SLOT_EQUIP_RE.match(k)
        if m:
            slots.setdefault(int(m.group(1)), {})["equip_name"] = v
            continue
        m = SLOT_EFF_RE.match(k)
        if m:
            slots.setdefault(int(m.group(1)), {})[f"eff_{m.group(2)}"] = v
            continue
        m = SLOT_TYPE_RE.match(k)
        if m:
            slots.setdefault(int(m.group(1)), {})["equip_type"] = v
    slot_rows = [
        {"ship_title": None, "slot": s, "equip_name": d.get("equip_name"),
         "equip_type": d.get("equip_type"), "eff_initial": d.get("eff_initial"),
         "eff_max": d.get("eff_max")}
        for s, d in sorted(slots.items())
    ]
    return ship, [{"title": None, "stat": s, "initial_value": d.get("initial"),
                   "max_value": d.get("max")} for s, d in stats.items()], skills, slot_rows


def extract_equipment(fields: dict[str, str], category: str) -> dict:
    return {
        "name": fields.get("名称"),
        "category": category,
        "equip_type": fields.get("类型"),
        "trait": fields.get("特性"),
        "rarity": fields.get("稀有度"),
        "faction": fields.get("所属阵营"),
        "damage": fields.get("伤害"),
        "rof": fields.get("标准射速"),
        "notes": fields.get("备注"),
    }


# ---------------------------------------------------------------- v3 新抽取器
STAGE_KEYMAP = {
    "map_no": ("地图编号",),
    "map_name": ("地图名",),
    "intro": ("地图介绍",),
    "clear_reward": ("通关奖励",),
    "three_star_cond": ("三星条件",),
    "three_star_reward": ("三星奖励",),
    "req_air": ("我方要求侦查值",),
    "enemy_air": ("敌方原始制空值",),
    "rec_air": ("我方推荐制空值",),
    "weekly_enemy_air": ("周回模式敌方原始制空值",),
    "weekly_rec_air": ("周回模式我方推荐制空值",),
    "ship_limit": ("舰船限定",),
    "enemy_level": ("敌舰等级",),
    "boss_level": ("BOSS等级",),
    "boss_pos": ("BOSS位置",),
    "boss_lineup": ("BOSS阵容",),
    "req_cmd_level": ("要求指挥官等级",),
    "danger": ("危险等级",),
    "oil_melee": ("周回道中油耗",),
    "oil_flagship": ("周回旗舰油耗",),
    "oil_sub": ("周回潜艇油耗",),
    "sub_start": ("潜艇起点",),
    "drops": ("地图掉落",),
}


def _first(fields: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = fields.get(k)
        if v:
            return v
    return None


def extract_stage(fields: dict[str, str]) -> dict:
    row = {col: _first(fields, keys) for col, keys in STAGE_KEYMAP.items()}
    if not row["map_name"]:
        row["map_name"] = fields.get("名称")
    return row


SHIP_PERF_KEYMAP = {
    "name": ("名称", "型号"),
    "displacement_std": ("排水量标准", "标准排水量"),
    "displacement_full": ("排水量满载", "满载排水量"),
    "length": ("长",),
    "beam": ("宽",),
    "draft": ("吃水",),
    "power": ("动力", "动力装置"),
    "speed": ("航速", "最大速度"),
    "range": ("最大航程", "航程"),
    "range_speed": ("最大航程所用航速",),
    "crew": ("编制",),
    "armament": ("武器", "机枪", "挂载"),
    "armor_": ("装甲",),
    "aircraft": ("舰载机",),
    "other": ("其它", "初始爬升率", "升限", "作战半径", "最大起飞重量", "翼展",
              "机长", "机高", "机翼面积", "重量", "装置"),
}


def extract_ship_perf(fields: dict[str, str], category: str) -> dict:
    row = {col: _first(fields, keys) for col, keys in SHIP_PERF_KEYMAP.items()}
    row.pop("armor_", None)
    row["perf_type"] = category
    return row


def extract_siren(fields: dict[str, str]) -> dict:
    return {
        "name": fields.get("名称"),
        "name_en": fields.get("英文名"),
        "faction": fields.get("所属") or fields.get("阵营"),
        "category": fields.get("分类"),
        "tarot": fields.get("塔罗牌"),
        "ship_type": fields.get("类型") or fields.get("舰种"),
        "unit_type": fields.get("兵种"),
        "code": fields.get("编号"),
        "belongs": fields.get("所属仲裁者") or fields.get("上级维序者"),
        "stages": fields.get("出现关卡"),
        "special_power": fields.get("特殊能力"),
        "intro": fields.get("介绍") or fields.get("相关介绍"),
    }


def extract_fan_ship(fields: dict[str, str]) -> dict:
    return {
        "name": fields.get("名称") or fields.get("名字"),
        "code": fields.get("编号"),
        "ship_type": fields.get("类型"),
        "faction": fields.get("阵营"),
        "author": fields.get("作者"),
        "identity": fields.get("身份"),
        "personality": fields.get("性格"),
        "keywords": fields.get("关键词"),
    }


def stage_card(title: str, row: dict) -> str:
    bits = [f"{row.get('map_no') or title}（{title}）"]
    if row.get("map_name"):
        bits.append(str(row["map_name"]))
    for label, key in (("敌方原始制空值", "enemy_air"), ("我方推荐制空值", "rec_air"),
                       ("三星条件", "three_star_cond"), ("三星奖励", "three_star_reward"),
                       ("通关奖励", "clear_reward"), ("敌舰等级", "enemy_level"),
                       ("BOSS等级", "boss_level"), ("BOSS位置", "boss_pos"),
                       ("BOSS阵容", "boss_lineup"), ("危险等级", "danger"),
                       ("潜艇起点", "sub_start"), ("地图掉落", "drops")):
        if row.get(key):
            bits.append(f"{label}{row[key]}")
    oil = "、".join(f"{lbl}{row[k]}" for lbl, k in
                    (("道中油耗", "oil_melee"), ("旗舰油耗", "oil_flagship"),
                     ("潜艇油耗", "oil_sub")) if row.get(k))
    if oil:
        bits.append(oil)
    if row.get("intro"):
        bits.append(f"介绍：{row['intro']}")
    return "。".join(bits) + "。"


def ship_perf_card(title: str, row: dict) -> str:
    bits = [f"{row.get('name') or title}（{title}）"]
    for label, key in (("标准排水量", "displacement_std"), ("满载排水量", "displacement_full"),
                       ("长", "length"), ("宽", "beam"), ("吃水", "draft"),
                       ("动力", "power"), ("航速", "speed"), ("最大航程", "range"),
                       ("编制", "crew"), ("武器", "armament"), ("装甲", "armor"),
                       ("舰载机", "aircraft")):
        if row.get(key):
            bits.append(f"{label}{row[key]}")
    return "。".join(bits) + "。"


def siren_card(title: str, row: dict) -> str:
    bits = [f"{row.get('name') or title}（{title}）"]
    for label, key in (("分类", "category"), ("塔罗牌", "tarot"), ("舰种", "ship_type"),
                       ("兵种", "unit_type"), ("所属", "faction"), ("编号", "code"),
                       ("上级", "belongs"), ("出现关卡", "stages"),
                       ("特殊能力", "special_power")):
        if row.get(key):
            bits.append(f"{label}{row[key]}")
    if row.get("intro"):
        bits.append(f"介绍：{row['intro']}")
    return "。".join(bits) + "。"


def fan_ship_card(title: str, row: dict) -> str:
    bits = [f"{row.get('name') or title}（{title}）"]
    for label, key in (("编号", "code"), ("类型", "ship_type"), ("阵营", "faction"),
                       ("身份", "identity"), ("性格", "personality"),
                       ("关键词", "keywords"), ("作者", "author")):
        if row.get(key):
            bits.append(f"{label}{row[key]}")
    return "。".join(bits) + "。"


# ---------------------------------------------------------------- 分节 / 分段
def iter_sections(text: str):
    """按标题切成 (标题路径, 正文) 段。"""
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush():
        if buf:
            body = "\n".join(buf)
            buf.clear()
            return body
        return None

    for line in text.split("\n"):
        m = HEADING_RE.match(line.strip())
        if m:
            body = flush()
            if body is not None:
                yield [t for _, t in stack], body
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    body = flush()
    if body is not None:
        yield [t for _, t in stack], body


def is_noise_heading(path: list[str]) -> bool:
    return any(any(k in h for k in NOISE_SECTION_KEYWORDS) for h in path)


def is_noise_paragraph(p: str) -> bool:
    if len(p) < CHUNK_MIN:
        return True
    return any(r.search(p) for r in NOISE_PARAGRAPH_RES)


def split_long(text: str, limit: int = CHUNK_MAX) -> list[str]:
    """超长段落按句子边界再切。"""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for sent in SENT_SPLIT_RE.split(text):
        if not sent:
            continue
        if len(cur) + len(sent) > limit and cur:
            out.append(cur.strip())
            cur = sent
        else:
            cur += sent
    if cur.strip():
        out.append(cur.strip())
    return out


def chunk_body(text: str) -> list[tuple[str, str]]:
    """正文纯文本 → [(标题路径, chunk 文本)]。"""
    paras: list[tuple[str, str]] = []  # (heading, paragraph)
    for path, body in iter_sections(text):
        if is_noise_heading(path):
            continue
        heading = " > ".join(path)
        body = cleantext.tables_to_lines(body)
        for para in re.split(r"\n\s*\n", body):
            # MediaWiki 语义：单个换行等于空格；表格行已由 tables_to_lines 压成一行
            para = re.sub(r"\s*\n\s*", " ", para)
            para = re.sub(r"[ \t]{2,}", " ", para).strip()
            if not para or is_noise_paragraph(para):
                continue
            paras.append((heading, para))

    return pack_paragraphs(paras)


def pack_paragraphs(paras: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(heading, 段落) 列表 → 合并到 CHUNK_TARGET、超长按句再切、丢短尾。"""
    chunks: list[tuple[str, str]] = []
    cur_head, cur = "", ""
    for heading, para in paras:
        if heading != cur_head and cur:
            chunks.append((cur_head, cur.strip()))
            cur = ""
        cur_head = heading
        for piece in split_long(para):
            if cur and len(cur) + len(piece) + 1 > CHUNK_TARGET:
                chunks.append((cur_head, cur.strip()))
                cur = ""
            cur = (cur + "\n" + piece).strip() if cur else piece
    if cur.strip():
        chunks.append((cur_head, cur.strip()))

    # 丢掉过短的尾巴
    return [(h, c) for h, c in chunks if len(c) >= CHUNK_MIN]


def pack_lines(lines: list[str], heading: str) -> list[tuple[str, str]]:
    """一批独立成义的行（图注/台词/模板参数）→ chunk，口径同 pack_paragraphs。"""
    return pack_paragraphs([(heading, line) for line in lines if line and line.strip()])


# ---------------------------------------------------------------- card 摘要
def ship_card(title: str, ship: dict, stats: dict, skills: list[dict]) -> str:
    parts = [f"{ship['name'] or title}（{title}）"]
    if ship.get("model"):
        parts.append(f"型号{ship['model']}")
    bits = []
    for k in ("faction", "rarity", "ship_type", "hull_type"):
        if ship.get(k):
            bits.append(str(ship[k]))
    if bits:
        parts.append("、".join(bits))
    if ship.get("code"):
        parts.append(f"编号{ship['code']}")
    for k in ("stars", "speed", "luck", "build_time"):
        if ship.get(k):
            parts.append(f"{ {'stars': '初始星级', 'speed': '航速', 'luck': '幸运', 'build_time': '建造时间'}[k] }{ship[k]}")
    stat_txt = "，".join(
        f"{k} {stats[k].get('initial', '?')}/{stats[k].get('max', '?')}" for k in SHIP_STATS if k in stats
    )
    if stat_txt:
        parts.append(f"属性（初始/满级）：{stat_txt}")
    names = [s["name"] for s in skills if s.get("name")]
    if names:
        parts.append("技能：" + "、".join(names))
    if ship.get("rating"):
        parts.append(f"评价：{ship['rating']}")
    return "。".join(p.strip() for p in parts if p and p.strip()) + "。"


def equip_card(title: str, eq: dict) -> str:
    bits = [f"{eq['name'] or title}（{title}）"]
    for label, key in (("类别", "category"), ("类型", "equip_type"), ("特性", "trait"),
                       ("稀有度", "rarity"), ("阵营", "faction"), ("伤害", "damage"),
                       ("标准射速", "rof")):
        if eq.get(key):
            bits.append(f"{label}{eq[key]}")
    if eq.get("notes"):
        bits.append(f"备注：{eq['notes']}")
    return "。".join(bits) + "。"


# ---------------------------------------------------------------- 主流程
def page_url(title: str) -> str:
    from urllib.parse import quote

    return f"{WIKI_BASE}/{quote(title.replace(' ', '_'))}"


def read_shards(source: Path):
    """按修改时间顺序读 source 下的 *.jsonl.gz。"""
    files = sorted(source.glob("*.jsonl.gz"))
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def load_image_urls(path: Path) -> dict[str, str]:
    """文件名 → 直链缓存（tools/fetch_images.py 批量 imageinfo 换来的）。"""
    if not path.exists():
        return {}
    try:
        return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if v}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("图片 URL 缓存读不了（%s），本次不记 images", e)
        return {}


def run(source: Path, db_path: Path, chunks_path: Path, report: bool = True) -> dict:
    conn = open_db(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    chunks_path.parent.mkdir(parents=True, exist_ok=True)

    image_urls = load_image_urls(IMAGE_URLS_PATH)
    infobox_names = set(INFOBOX_TEMPLATES)
    n_pages = n_ok = 0
    tpl_counter: dict[str, int] = {}
    kind_counter: dict[str, int] = {}
    extra_counter: dict[str, int] = {}
    n_chunks = n_extra = n_images = 0
    failures: list[str] = []

    with open(chunks_path, "w", encoding="utf-8") as cf:
        # chunk_id 分配：prose/card 走老计数器（v2 已入向量库的 id 一个都不能变，
        # 否则增量 upsert 会退化成全量重建）；v3 新增的补捞内容走 x 前缀的另一个
        # 计数器，两边互不干扰
        def emit(text: str, heading: str, kind: str, title: str, url: str,
                 images: list[str]) -> None:
            nonlocal n_extra
            rec = {
                "chunk_id": f"blhx:x{n_extra:07d}",
                "text": text,
                "source_title": title,
                "source_url": url,
                "heading": heading,
                "kind": kind,
            }
            if images:
                rec["images"] = images
            cf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_extra += 1
            extra_counter[kind] = extra_counter.get(kind, 0) + 1

        def emit_pack(lines: list[str], heading: str, kind: str, title: str,
                      url: str, images: list[str]) -> None:
            for _h, chunk in pack_lines(lines, heading):
                emit(chunk, heading, kind, title, url, images)

        for rec in read_shards(source):
            title = rec["title"]
            # 先过 preprocess：修 mwparserfromhell 的括号/标签配对错误
            #（{{#块内[[链接]] 提前截断模板、标签落模板名），否则整个 infobox
            # 连参数一起静默消失。
            wikitext = cleantext.preprocess(rec["wikitext"])
            n_pages += 1
            found = cleantext.top_level_infobox(wikitext, infobox_names,
                                               LEGACY_INFOBOX_TEMPLATE_NAMES)
            kind, category, fields = "other", None, {}
            body_wikitext = wikitext

            if found:
                tpl, body_wikitext = found
                # 塞壬图鉴/仲裁机关 这类家族子页归一成家族名再查表
                tpl_name = cleantext.infobox_template_name(tpl)
                kind, category = TEMPLATE_KINDS[tpl_name]
                fields = cleantext.infobox_fields(tpl)
                tpl_counter[tpl_name] = tpl_counter.get(tpl_name, 0) + 1
            elif STAGE_RE.match(title):
                kind = "stage"

            infobox_ok = 1 if len(fields) >= 5 else 0
            if infobox_ok:
                n_ok += 1
            kind_counter[kind] = kind_counter.get(kind, 0) + 1

            # ---- 结构化落库 ----
            # v3 新家族（关卡图鉴/原型舰/塞壬/同人角色）把入库门槛降到"有字段就行"：
            # 全库有 6 页字段不足 5 个，其中 3 页带着地图介绍/解锁条件，漏掉可惜。
            # 只降新家族的门槛——老三族的 card 走老 chunk_id 计数器，条件一改 id 就错位。
            # 新家族的 card 走 x 前缀计数器，随便降。
            row_ok = 1 if (kind in NEW_KINDS and fields) else infobox_ok
            ship_row = stats_rows = skill_rows = slot_rows = None
            card_text = None
            if kind == "ship" and infobox_ok:
                ship, stats_rows, skill_rows, slot_rows = extract_ship(fields)
                stats_map = {r["stat"]: {"initial": r["initial_value"], "max": r["max_value"]}
                             for r in stats_rows}
                card_text = ship_card(title, ship, stats_map, skill_rows)
                ship_row = ship
            elif kind == "equipment" and infobox_ok:
                eq = extract_equipment(fields, category)
                card_text = equip_card(title, eq)
            elif kind == "stage" and row_ok:
                row = extract_stage(fields)
                card_text = stage_card(title, row)
                conn.execute(
                    "INSERT OR REPLACE INTO stages(page_id,title,map_no,map_name,intro,clear_reward,"
                    "three_star_cond,three_star_reward,req_air,enemy_air,rec_air,weekly_enemy_air,"
                    "weekly_rec_air,ship_limit,enemy_level,boss_level,boss_pos,boss_lineup,"
                    "req_cmd_level,danger,oil_melee,oil_flagship,oil_sub,sub_start,drops,fields_json)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.get("page_id"), title, row["map_no"], row["map_name"], row["intro"],
                     row["clear_reward"], row["three_star_cond"], row["three_star_reward"],
                     row["req_air"], row["enemy_air"], row["rec_air"], row["weekly_enemy_air"],
                     row["weekly_rec_air"], row["ship_limit"], row["enemy_level"], row["boss_level"],
                     row["boss_pos"], row["boss_lineup"], row["req_cmd_level"], row["danger"],
                     row["oil_melee"], row["oil_flagship"], row["oil_sub"], row["sub_start"],
                     row["drops"], json.dumps(fields, ensure_ascii=False)),
                )
            elif kind == "ship_perf" and row_ok:
                row = extract_ship_perf(fields, category)
                card_text = ship_perf_card(title, row)
                conn.execute(
                    "INSERT OR REPLACE INTO ship_perf(page_id,title,name,perf_type,displacement_std,"
                    "displacement_full,length,beam,draft,power,speed,range,range_speed,crew,armament,"
                    "armor,aircraft,other,fields_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.get("page_id"), title, row["name"], row["perf_type"],
                     row["displacement_std"], row["displacement_full"], row["length"], row["beam"],
                     row["draft"], row["power"], row["speed"], row["range"], row["range_speed"],
                     row["crew"], row["armament"], row.get("armor"), row["aircraft"], row["other"],
                     json.dumps(fields, ensure_ascii=False)),
                )
            elif kind == "siren" and row_ok:
                row = extract_siren(fields)
                card_text = siren_card(title, row)
                conn.execute(
                    "INSERT OR REPLACE INTO sirens(page_id,title,name,name_en,faction,category,tarot,"
                    "ship_type,unit_type,code,belongs,stages,special_power,intro,fields_json)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.get("page_id"), title, row["name"], row["name_en"], row["faction"],
                     row["category"], row["tarot"], row["ship_type"], row["unit_type"], row["code"],
                     row["belongs"], row["stages"], row["special_power"], row["intro"],
                     json.dumps(fields, ensure_ascii=False)),
                )
            elif kind == "fan_ship" and row_ok:
                row = extract_fan_ship(fields)
                card_text = fan_ship_card(title, row)
                conn.execute(
                    "INSERT OR REPLACE INTO fan_ships(page_id,title,name,code,ship_type,faction,"
                    "author,identity,personality,keywords,fields_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.get("page_id"), title, row["name"], row["code"], row["ship_type"],
                     row["faction"], row["author"], row["identity"], row["personality"],
                     row["keywords"], json.dumps(fields, ensure_ascii=False)),
                )

            conn.execute(
                "INSERT OR REPLACE INTO pages(page_id,title,kind,url,wikitext_len,infobox_template,"
                "infobox_fields,infobox_ok) VALUES(?,?,?,?,?,?,?,?)",
                (rec.get("page_id"), title, kind, page_url(title), len(wikitext),
                 str(found[0].name).strip() if found else None, len(fields), infobox_ok),
            )

            if ship_row:
                conn.execute(
                    "INSERT OR REPLACE INTO ships(page_id,title,name,model,name_en,name_jp,code,ship_type,"
                    "rarity,faction,stars,hull_type,speed,luck,build_time,acquisition,rating,notes,cv,painter,"
                    "fields_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.get("page_id"), title, ship_row["name"], ship_row["model"], ship_row["name_en"],
                     ship_row["name_jp"], ship_row["code"], ship_row["ship_type"], ship_row["rarity"],
                     ship_row["faction"], ship_row["stars"], ship_row["hull_type"], ship_row["speed"],
                     ship_row["luck"], ship_row["build_time"], ship_row["acquisition"], ship_row["rating"],
                     ship_row["notes"], ship_row["cv"], ship_row["painter"],
                     json.dumps(fields, ensure_ascii=False)),
                )
                for r in stats_rows:
                    conn.execute("INSERT OR REPLACE INTO ship_stats VALUES(?,?,?,?)",
                                 (title, r["stat"], r["initial_value"], r["max_value"]))
                for r in skill_rows:
                    conn.execute("INSERT OR REPLACE INTO skills VALUES(?,?,?,?,?)",
                                 (title, r["slot"], r["variant"], r["name"], r["description"]))
                for r in slot_rows:
                    conn.execute("INSERT OR REPLACE INTO ship_slots VALUES(?,?,?,?,?,?)",
                                 (title, r["slot"], r["equip_name"], r["equip_type"],
                                  r["eff_initial"], r["eff_max"]))
            elif kind == "equipment" and infobox_ok:
                conn.execute(
                    "INSERT OR REPLACE INTO equipments(page_id,title,name,category,equip_type,trait,rarity,"
                    "faction,damage,rof,notes,fields_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.get("page_id"), title, eq["name"], eq["category"], eq["equip_type"], eq["trait"],
                     eq["rarity"], eq["faction"], eq["damage"], eq["rof"], eq["notes"],
                     json.dumps(fields, ensure_ascii=False)),
                )
            elif kind == "cat" and infobox_ok:
                conn.execute(
                    "INSERT OR REPLACE INTO cats(page_id,title,name,rarity,fields_json) VALUES(?,?,?,?,?)",
                    (rec.get("page_id"), title, fields.get("名称"), fields.get("稀有度"),
                     json.dumps(fields, ensure_ascii=False)),
                )

            # ---- 图片 URL（只记链接不下载）----
            # 注意：补捞用的三个正则函数（extract_image_files / gallery_lines /
            # hidden_lines 里的 COMMENT_RE）全部直接扫原始 wikitext，不过
            # mwparserfromhell，preprocess 对它们只有负作用——`{{#ask:…[[文件:x.jpg]]…}}`
            # 里的文件引用会被折成纯文本，FILE_REF_RE 就再也匹配不到。
            # 实测：不传原文会让 16 个图鉴导航页少 552 个图片直链。
            # 所以图片/图鉴走原文；hidden_lines 留给预处理版（它内部要用
            # mwparserfromhell 渲染注释正文，预处理能把 `{{#info:}}` 残迹清干净，
            # 且注释内容已逐页核对无丢失）。
            url = page_url(title)
            images: list[str] = []
            for sort, fname in enumerate(cleantext.extract_image_files(rec["wikitext"])):
                u = image_urls.get(fname)
                conn.execute("INSERT OR REPLACE INTO page_images VALUES(?,?,?,?)",
                             (title, sort, fname, u))
                if u:
                    images.append(u)
            n_images += len(images)

            # ---- 文本路：正文 → chunk ----
            body_text = cleantext.to_plain(body_wikitext)
            for heading, chunk in chunk_body(body_text):
                cf.write(json.dumps({
                    "chunk_id": f"blhx:{n_chunks:07d}",
                    "text": chunk,
                    "source_title": title,
                    "source_url": url,
                    "heading": heading,
                    "kind": "prose",
                    **({"images": images} if images else {}),
                }, ensure_ascii=False) + "\n")
                n_chunks += 1
            if card_text:
                # 老三类（舰娘/装备/指挥喵）的 card 还走老计数器，v2 已入库的 id
                # 一个都不能动；v3 新图鉴的 card 走 x 前缀，否则会把后面的 prose id 全挤位
                if kind in NEW_KINDS:
                    emit(card_text, "卡片摘要", "card", title, url, images)
                else:
                    cf.write(json.dumps({
                        "chunk_id": f"blhx:{n_chunks:07d}",
                        "text": card_text,
                        "source_title": title,
                        "source_url": url,
                        "heading": "卡片摘要",
                        "kind": "card",
                        **({"images": images} if images else {}),
                    }, ensure_ascii=False) + "\n")
                    n_chunks += 1

            # ---- v3 补捞：正文路径丢过/只取了首参的内容，另切一类 chunk ----
            # gallery_lines 同 extract_image_files：纯正则扫原文，preprocess 只会
            # 让它少匹配（`{{#}}` 块内的 <gallery> 会被 _PF_DROP_BLOCK 整块删掉）。
            emit_pack(cleantext.gallery_lines(rec["wikitext"]), "图库", "gallery",
                      title, url, images)
            emit_pack(cleantext.hidden_lines(wikitext), "隐藏正文", "hidden", title, url, images)
            emit_pack(cleantext.template_dump(body_wikitext), "模板数据", "template",
                      title, url, images)
            if fields:
                voice = [f"{scene}：{line}" for scene, line in cleantext.voice_lines(fields)]
                emit_pack(voice, "语音台词", "voice", title, url, images)
                skip = CARD_FIELD_KEYS.get(kind, frozenset())
                long_fields = [ln for ln in cleantext.long_field_lines(fields)
                               if ln.split(":", 1)[0] not in skip]
                emit_pack(long_fields, "图鉴字段", "fields", title, url, images)

            if found and not infobox_ok and not row_ok:
                failures.append(title)

    # ---- 重定向别名（内容在目标页里，别名本身也要能查到）----
    n_aliases = 0
    if REDIRECTS_PATH.exists():
        with gzip.open(REDIRECTS_PATH, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("alias") and d.get("target"):
                    conn.execute("INSERT OR REPLACE INTO aliases VALUES(?,?)",
                                 (d["alias"], d["target"]))
                    n_aliases += 1

    for k, v in (("pages", n_pages), ("infobox_ok", n_ok), ("chunks", n_chunks),
                 ("extra_chunks", n_extra), ("aliases", n_aliases),
                 ("page_images", n_images),
                 ("infobox_rate", round(n_ok / max(n_pages, 1), 4))):
        conn.execute("INSERT OR REPLACE INTO parse_stats VALUES(?,?)", (k, str(v)))
    conn.commit()
    conn.close()

    result = {
        "pages": n_pages,
        "infobox_pages": sum(tpl_counter.values()),
        "infobox_ok": n_ok,
        "infobox_rate": round(n_ok / max(sum(tpl_counter.values()), 1), 4),
        "overall_rate": round(n_ok / max(n_pages, 1), 4),
        "templates": dict(sorted(tpl_counter.items(), key=lambda x: -x[1])),
        "kinds": kind_counter,
        "chunks": n_chunks,
        "extra_chunks": n_extra,
        "extra_by_kind": dict(sorted(extra_counter.items(), key=lambda x: -x[1])),
        "page_images": n_images,
        "aliases": n_aliases,
        "weak_infobox": failures[:50],
    }
    if report:
        print(f"页面数        : {n_pages}")
        print(f"带 infobox 页 : {result['infobox_pages']}")
        print(f"抽取成功      : {n_ok}")
        print(f"结构化抽取率  : {result['infobox_rate']:.1%}（占带 infobox 页）")
        print(f"全页占比      : {result['overall_rate']:.1%}")
        print("模板分布      :")
        for k, v in result["templates"].items():
            print(f"    {k:<16} {v}")
        print(f"页面类型      : {kind_counter}")
        print(f"chunk 数      : {n_chunks}（prose/card，id 与 v2 对齐）")
        print(f"补捞 chunk   : {n_extra}  {result['extra_by_kind']}")
        print(f"图片 URL     : {n_images}（{len(image_urls)} 个文件名已换直链）")
        print(f"重定向别名    : {n_aliases}")
        if failures:
            print(f"抽取不完整示例 : {failures[:10]}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="解析 wikitext → SQLite + chunk JSONL")
    ap.add_argument("--source", type=Path, default=RAW_DIR, help="raw 分片目录")
    ap.add_argument("--db", type=Path, default=KB_DB)
    ap.add_argument("--chunks", type=Path, default=CHUNKS_PATH)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not args.source.exists():
        print(f"找不到 {args.source}，先跑 fetch.py", file=sys.stderr)
        return 1
    run(args.source, args.db, args.chunks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
