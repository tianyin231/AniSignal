"""碧蓝航线专用抽取器：从 parse.py 原样搬过来，一行逻辑没改。

为什么单独放：字段语义（初始/满级属性、技能N名、N号槽装备效率）和表结构
（ships / ship_stats / skills / ship_slots / stages / ship_perf / sirens / fan_ships）
都是碧蓝航线特有的，写死在代码里比塞配置更清楚。别的游戏不挂这个模块
（games/*.yaml 的 extractor_module 决定），kind 名撞车也不会串味。

每款游戏一个模块，靠 EXTARCTORS 字典把 kind 映射到抽取函数；strict=True 的
抽取器要求 infobox 字段数达标（老三族的 card 走 v2 老 chunk_id 计数器，
门槛一改 id 就错位），strict=False 的是 v3 新家族（card 走 x 前缀，宽松点没事）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import Row, ExtractCtx, ExtractResult

SHIP_STATS = [
    "耐久", "炮击", "雷击", "航空", "装填", "命中", "机动", "防空", "反潜", "消耗",
]

SKILL_RE = re.compile(r"^技能(\d+)(名)?(.*)$")
SLOT_EFF_RE = re.compile(r"^(\d+)号槽装备效率(初始|满破)$")
SLOT_TYPE_RE = re.compile(r"^(\d+)号槽装备类型$")
SLOT_EQUIP_RE = re.compile(r"^装备(\d+)$")

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
    # v3 的怪癖，照抄：装甲映射到 armor_ 然后当场 pop 掉，只进 fields_json，
    # 不进 card。于是 _ship_perf_card 里那个 ("装甲", "armor") 标签永远不触发。
    # 别"修好"它——11 个原型舰页的 card 文本一变，blhx_kb 里对应向量就失效了。
    "armor_": ("装甲",),
    "aircraft": ("舰载机",),
    "other": ("其它", "初始爬升率", "升限", "作战半径", "最大起飞重量", "翼展",
              "机长", "机高", "机翼面积", "重量", "装置"),
}


def _num(v: str | None) -> float | None:
    if not v:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
    return float(m.group()) if m else None


def _first(fields: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = fields.get(k)
        if v:
            return v
    return None


def _fields_json(fields: dict[str, str]) -> str:
    return json.dumps(fields, ensure_ascii=False)


# ---------------------------------------------------------------- 卡片摘要
def _ship_card(title: str, ship: dict, stats: dict, skills: list[dict]) -> str:
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
            parts.append(
                f"{ {'stars': '初始星级', 'speed': '航速', 'luck': '幸运', 'build_time': '建造时间'}[k] }{ship[k]}")
    stat_txt = "，".join(
        f"{k} {stats[k].get('initial', '?')}/{stats[k].get('max', '?')}"
        for k in SHIP_STATS if k in stats
    )
    if stat_txt:
        parts.append(f"属性（初始/满级）：{stat_txt}")
    names = [s["name"] for s in skills if s.get("name")]
    if names:
        parts.append("技能：" + "、".join(names))
    if ship.get("rating"):
        parts.append(f"评价：{ship['rating']}")
    return "。".join(p.strip() for p in parts if p and p.strip()) + "。"


def _equip_card(title: str, eq: dict) -> str:
    bits = [f"{eq['name'] or title}（{title}）"]
    for label, key in (("类别", "category"), ("类型", "equip_type"), ("特性", "trait"),
                       ("稀有度", "rarity"), ("阵营", "faction"), ("伤害", "damage"),
                       ("标准射速", "rof")):
        if eq.get(key):
            bits.append(f"{label}{eq[key]}")
    if eq.get("notes"):
        bits.append(f"备注：{eq['notes']}")
    return "。".join(bits) + "。"


def _stage_card(title: str, row: dict) -> str:
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


def _ship_perf_card(title: str, row: dict) -> str:
    """原型舰性能卡片。注意 ("装甲", "armor") 因上面的 pop 永不触发，保留原样。"""
    bits = [f"{row.get('name') or title}（{title}）"]
    for label, key in (("标准排水量", "displacement_std"), ("满载排水量", "displacement_full"),
                       ("长", "length"), ("宽", "beam"), ("吃水", "draft"),
                       ("动力", "power"), ("航速", "speed"), ("最大航程", "range"),
                       ("编制", "crew"), ("武器", "armament"), ("装甲", "armor"),
                       ("舰载机", "aircraft")):
        if row.get(key):
            bits.append(f"{label}{row[key]}")
    return "。".join(bits) + "。"


def _siren_card(title: str, row: dict) -> str:
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


def _fan_ship_card(title: str, row: dict) -> str:
    bits = [f"{row.get('name') or title}（{title}）"]
    for label, key in (("编号", "code"), ("类型", "ship_type"), ("阵营", "faction"),
                       ("身份", "identity"), ("性格", "personality"),
                       ("关键词", "keywords"), ("作者", "author")):
        if row.get(key):
            bits.append(f"{label}{row[key]}")
    return "。".join(bits) + "。"


# ---------------------------------------------------------------- 抽取器
def extract_ship(ctx: ExtractCtx) -> ExtractResult:
    fields = ctx.fields
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
        skills.append({"slot": slot, "variant": variant,
                       "name": name or None, "description": desc or None})

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

    stat_rows = [{"stat": s, "initial_value": d.get("initial"), "max_value": d.get("max")}
                 for s, d in stats.items()]
    stats_map = {r["stat"]: {"initial": r["initial_value"], "max": r["max_value"]}
                 for r in stat_rows}

    rows = [Row("ships", {"page_id": ctx.page_id, "title": ctx.title, **ship,
                          "fields_json": _fields_json(fields)})]
    for r in stat_rows:
        rows.append(Row("ship_stats", {"title": ctx.title, **r}))
    for r in skills:
        rows.append(Row("skills", {"ship_title": ctx.title, **r}))
    for s, d in sorted(slots.items()):
        rows.append(Row("ship_slots", {
            "ship_title": ctx.title, "slot": s, "equip_name": d.get("equip_name"),
            "equip_type": d.get("equip_type"), "eff_initial": d.get("eff_initial"),
            "eff_max": d.get("eff_max")}))
    return ExtractResult(rows=rows, card_text=_ship_card(ctx.title, ship, stats_map, skills))


def extract_equipment(ctx: ExtractCtx) -> ExtractResult:
    fields = ctx.fields
    eq = {
        "name": fields.get("名称"),
        "category": ctx.category,
        "equip_type": fields.get("类型"),
        "trait": fields.get("特性"),
        "rarity": fields.get("稀有度"),
        "faction": fields.get("所属阵营"),
        "damage": fields.get("伤害"),
        "rof": fields.get("标准射速"),
        "notes": fields.get("备注"),
    }
    return ExtractResult(
        rows=[Row("equipments", {"page_id": ctx.page_id, "title": ctx.title, **eq,
                                 "fields_json": _fields_json(fields)})],
        card_text=_equip_card(ctx.title, eq),
    )


def extract_cat(ctx: ExtractCtx) -> ExtractResult:
    fields = ctx.fields
    # 指挥喵没有卡片摘要 chunk（v1 起就没有，加了会改动老 chunk_id 计数）
    return ExtractResult(
        rows=[Row("cats", {"page_id": ctx.page_id, "title": ctx.title,
                           "name": fields.get("名称"), "rarity": fields.get("稀有度"),
                           "fields_json": _fields_json(fields)})],
    )


def extract_stage(ctx: ExtractCtx) -> ExtractResult:
    fields = ctx.fields
    row = {col: _first(fields, keys) for col, keys in STAGE_KEYMAP.items()}
    if not row["map_name"]:
        row["map_name"] = fields.get("名称")
    return ExtractResult(
        rows=[Row("stages", {"page_id": ctx.page_id, "title": ctx.title, **row,
                             "fields_json": _fields_json(fields)})],
        card_text=_stage_card(ctx.title, row),
    )


def extract_ship_perf(ctx: ExtractCtx) -> ExtractResult:
    fields = ctx.fields
    row = {col: _first(fields, keys) for col, keys in SHIP_PERF_KEYMAP.items()}
    row.pop("armor_", None)   # 同上：装甲只留 fields_json，card 里不出现
    row["perf_type"] = ctx.category
    return ExtractResult(
        rows=[Row("ship_perf", {"page_id": ctx.page_id, "title": ctx.title, **row,
                                "fields_json": _fields_json(fields)})],
        card_text=_ship_perf_card(ctx.title, row),
    )


def extract_siren(ctx: ExtractCtx) -> ExtractResult:
    fields = ctx.fields
    row = {
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
    return ExtractResult(
        rows=[Row("sirens", {"page_id": ctx.page_id, "title": ctx.title, **row,
                             "fields_json": _fields_json(fields)})],
        card_text=_siren_card(ctx.title, row),
    )


def extract_fan_ship(ctx: ExtractCtx) -> ExtractResult:
    fields = ctx.fields
    row = {
        "name": fields.get("名称") or fields.get("名字"),
        "code": fields.get("编号"),
        "ship_type": fields.get("类型"),
        "faction": fields.get("阵营"),
        "author": fields.get("作者"),
        "identity": fields.get("身份"),
        "personality": fields.get("性格"),
        "keywords": fields.get("关键词"),
    }
    return ExtractResult(
        rows=[Row("fan_ships", {"page_id": ctx.page_id, "title": ctx.title, **row,
                                "fields_json": _fields_json(fields)})],
        card_text=_fan_ship_card(ctx.title, row),
    )


# kind → 抽取器。strict=True 的四个老三族要求 infobox 字段数达标
# （card 走 v2 老 chunk_id 计数器，门槛一改 id 就错位）；v3 新家族宽松。
EXTRACTORS: dict[str, Any] = {
    "ship": (extract_ship, True),
    "equipment": (extract_equipment, True),
    "cat": (extract_cat, True),
    "stage": (extract_stage, False),
    "ship_perf": (extract_ship_perf, False),
    "siren": (extract_siren, False),
    "fan_ship": (extract_fan_ship, False),
}

SCHEMA = """
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

"""
