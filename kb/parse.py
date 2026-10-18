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
    INFOBOX_TEMPLATES,
    KB_DB,
    NOISE_SECTION_KEYWORDS,
    RAW_DIR,
    SPIKE_DIR,
    WIKI_BASE,
)

log = logging.getLogger("kb.parse")

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


def run(source: Path, db_path: Path, chunks_path: Path, report: bool = True) -> dict:
    conn = open_db(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    chunks_path.parent.mkdir(parents=True, exist_ok=True)

    infobox_names = set(INFOBOX_TEMPLATES)
    n_pages = n_ok = 0
    tpl_counter: dict[str, int] = {}
    kind_counter: dict[str, int] = {}
    n_chunks = 0
    failures: list[str] = []

    with open(chunks_path, "w", encoding="utf-8") as cf:
        for rec in read_shards(source):
            title = rec["title"]
            wikitext = rec["wikitext"]
            n_pages += 1
            found = cleantext.top_level_infobox(wikitext, infobox_names)
            kind, category, fields = "other", None, {}
            body_wikitext = wikitext

            if found:
                tpl, body_wikitext = found
                tpl_name = str(tpl.name).strip()
                kind, category = INFOBOX_TEMPLATES[tpl_name]
                fields = cleantext.infobox_fields(tpl)
                tpl_counter[tpl_name] = tpl_counter.get(tpl_name, 0) + 1
            elif STAGE_RE.match(title):
                kind = "stage"

            infobox_ok = 1 if len(fields) >= 5 else 0
            if infobox_ok:
                n_ok += 1
            kind_counter[kind] = kind_counter.get(kind, 0) + 1

            # ---- 结构化落库 ----
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

            # ---- 文本路：正文 → chunk ----
            body_text = cleantext.to_plain(body_wikitext)
            url = page_url(title)
            for heading, chunk in chunk_body(body_text):
                cf.write(json.dumps({
                    "chunk_id": f"blhx:{n_chunks:07d}",
                    "text": chunk,
                    "source_title": title,
                    "source_url": url,
                    "heading": heading,
                    "kind": "prose",
                }, ensure_ascii=False) + "\n")
                n_chunks += 1
            if card_text:
                cf.write(json.dumps({
                    "chunk_id": f"blhx:{n_chunks:07d}",
                    "text": card_text,
                    "source_title": title,
                    "source_url": url,
                    "heading": "卡片摘要",
                    "kind": "card",
                }, ensure_ascii=False) + "\n")
                n_chunks += 1

            if found and not infobox_ok:
                failures.append(title)

    for k, v in (("pages", n_pages), ("infobox_ok", n_ok), ("chunks", n_chunks),
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
        print(f"chunk 数      : {n_chunks}")
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
