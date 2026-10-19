#!/usr/bin/env python3
"""生成鸣潮评测题库（50 题：30 查表 + 20 问答）。

    ./.venv/bin/python tools/gen_mingchao_eval.py            # 生成 + 自检
    ./.venv/bin/python tools/gen_mingchao_eval.py --force    # 覆盖已存在的题库

题库不是手写死的：查表题的 `expect` 全部**当场从 kb.db 里取实值**，问答题的
关键词全部**当场回 chunks.jsonl 里验一遍确实出现在来源页**。任何一条取不到值/
关键词不在页内，就直接报错退出——题库里出现"期望一个库里根本没有的值"比题库
小更糟（eval 全红，但你根本不知道该修管线还是该修题）。

查表题走 eval.py 的通用探针（entity_attr / entity_json / child / child_list），
表名列名都在 games/mingchao.yaml 的 child_templates 里声明过，探针自带白名单。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kblib import gamecfg  # noqa: E402
from kblib.extractors import child_table_columns  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "eval" / "mingchao" / "questions.jsonl"

# (id, 问题, probe, args)。args 里的值会原样传给探针，expect 由探针实跑结果回填
TABLE_SPECS: list[tuple[str, str, str, dict]] = [
    # ---- entity_attr：共鸣者基本资料（8 题）----
    ("t01", "今汐的属性是什么", "entity_attr", {"title": "共鸣者/今汐", "attr": "属性"}),
    ("t02", "长离使用什么武器", "entity_attr", {"title": "共鸣者/长离", "attr": "武器"}),
    ("t03", "忌炎的势力是哪一方", "entity_attr", {"title": "共鸣者/忌炎", "attr": "势力"}),
    ("t04", "卡卡罗的共鸣能力叫什么", "entity_attr", {"title": "共鸣者/卡卡罗", "attr": "共鸣能力"}),
    ("t05", "维里奈的战斗风格定位是什么", "entity_attr",
     {"title": "共鸣者/维里奈", "attr": "战斗风格"}),
    ("t06", "今汐的实装日期是哪天", "entity_attr",
     {"title": "共鸣者/今汐", "attr": "实装日期"}),
    ("t07", "今汐的品质是几星", "entity_attr", {"title": "共鸣者/今汐", "attr": "品质"}),
    ("t08", "散华的共鸣能力叫什么", "entity_attr",
     {"title": "共鸣者/散华", "attr": "共鸣能力"}),
    # ---- entity_json：只在 fields_json 里的字段（3 题）----
    ("t09", "今汐的英文名是什么", "entity_json",
     {"title": "共鸣者/今汐", "key": "英文名"}),
    ("t10", "克里斯托弗的日文名称是什么", "entity_json",
     {"title": "简介/克里斯托弗", "key": "日文名称"}),
    ("t11", "阿维狄亚的英文名称是什么", "entity_json",
     {"title": "简介/阿维狄亚", "key": "英文名称"}),
    # ---- mc_resonator_stats：等级属性表（6 题）----
    ("t12", "今汐1级的生命是多少", "child",
     {"table": "mc_resonator_stats", "title": "共鸣者/今汐", "column": "value",
      "where": {"level": "基础", "stat": "生命"}}),
    ("t13", "今汐90级的攻击是多少", "child",
     {"table": "mc_resonator_stats", "title": "共鸣者/今汐", "column": "value",
      "where": {"level": "90", "stat": "攻击"}}),
    ("t14", "长离90级的防御是多少", "child",
     {"table": "mc_resonator_stats", "title": "共鸣者/长离", "column": "value",
      "where": {"level": "90", "stat": "防御"}}),
    ("t15", "忌炎20级的生命是多少", "child",
     {"table": "mc_resonator_stats", "title": "共鸣者/忌炎", "column": "value",
      "where": {"level": "20", "stat": "生命"}}),
    ("t16", "维里奈90级的攻击是多少", "child",
     {"table": "mc_resonator_stats", "title": "共鸣者/维里奈", "column": "value",
      "where": {"level": "90", "stat": "攻击"}}),
    ("t17", "维里奈50级的防御是多少", "child",
     {"table": "mc_resonator_stats", "title": "共鸣者/维里奈", "column": "value",
      "where": {"level": "50", "stat": "防御"}}),
    # ---- mc_weapon_ascension：武器突破材料（3 题）----
    ("t18", "永夜长明20级突破需要什么材料", "child",
     {"table": "mc_weapon_ascension", "title": "武器/永夜长明", "column": "materials",
      "where": {"level": "20"}}),
    ("t19", "永夜长明50级突破需要什么材料", "child",
     {"table": "mc_weapon_ascension", "title": "武器/永夜长明", "column": "materials",
      "where": {"level": "50"}}),
    ("t20", "万物持存的注释70级突破需要什么材料", "child",
     {"table": "mc_weapon_ascension", "title": "武器/万物持存的注释",
      "column": "materials", "where": {"level": "70"}}),
    # ---- mc_resonator_chains / skills / stories（7 题）----
    ("t21", "今汐的共鸣链1叫什么", "child",
     {"table": "mc_resonator_chains", "title": "共鸣者/今汐", "column": "name",
      "where": {"index": "1"}}),
    ("t22", "今汐的共鸣链3叫什么", "child",
     {"table": "mc_resonator_chains", "title": "共鸣者/今汐", "column": "name",
      "where": {"index": "3"}}),
    ("t23", "长离的共鸣链2叫什么", "child",
     {"table": "mc_resonator_chains", "title": "共鸣者/长离", "column": "name",
      "where": {"index": "2"}}),
    ("t24", "今汐的常态攻击叫什么", "child",
     {"table": "mc_resonator_skills", "title": "共鸣者/今汐",
      "column": "basic_attack", "where": {}}),
    ("t25", "卡卡罗的共鸣解放叫什么", "child",
     {"table": "mc_resonator_skills", "title": "共鸣者/卡卡罗",
      "column": "liberation", "where": {}}),
    ("t26", "今汐的故事1叫什么", "child",
     {"table": "mc_resonator_stories", "title": "共鸣者/今汐", "column": "name",
      "where": {"index": "1"}}),
    ("t27", "今汐的珍贵之物1叫什么", "child",
     {"table": "mc_resonator_stories", "title": "共鸣者/今汐", "column": "name",
      "where": {"index": None, "template": "共鸣者/档案"}}),
    # ---- mc_exploration_reports + child_list（3 题）----
    ("t28", "中曲台地50%探索报告的标题是什么", "child",
     {"table": "mc_exploration_reports", "title": "探索报告/中曲台地",
      "column": "title", "where": {"percent": "50"}}),
    ("t29", "云陵谷100%探索报告的标题是什么", "child",
     {"table": "mc_exploration_reports", "title": "探索报告/云陵谷",
      "column": "title", "where": {"percent": "100"}}),
    ("t30", "今汐的六条共鸣链分别叫什么", "child_list",
     {"table": "mc_resonator_chains", "title": "共鸣者/今汐", "column": "name",
      "where": {}, "sep": "；"}),
]

# (id, 问题, [关键词...], 来源页)。关键词必须出现在该页的某个 chunk 里
QA_SPECS: list[tuple[str, str, list[str], str]] = [
    ("q01", "今汐的共鸣能力是什么", ["寒尽觉春生"], "共鸣者/今汐"),
    ("q02", "今汐的共鸣链1有什么效果", ["惊蛰"], "共鸣者/今汐"),
    ("q03", "今汐的故事令尹之名讲了什么", ["今州城"], "共鸣者/今汐"),
    ("q04", "长离的属性是什么", ["热熔"], "共鸣者/长离"),
    ("q05", "长离的特殊料理是什么", ["雀翎辣肉"], "共鸣者/长离"),
    ("q06", "卡卡罗的共鸣解放叫什么", ["幻影蚀刻"], "共鸣者/卡卡罗"),
    ("q07", "永夜长明的武器技能有什么效果", ["变奏技能"], "武器/永夜长明"),
    ("q08", "聚械机偶的声骸技能造成什么伤害", ["导电"], "声骸/聚械机偶"),
    ("q09", "不绝余音五件套效果是什么", ["延奏技能伤害"], "声骸合鸣/不绝余音"),
    ("q10", "无妄者是什么样的敌人", ["呓语种残象"], "敌人/无妄者"),
    ("q11", "罗蕾莱原本是什么生物", ["回音生物"], "敌人/罗蕾莱"),
    ("q12", "清芬鱼汤是做什么用的", ["耐力"], "道具/清芬鱼汤"),
    ("q13", "辉萤军势是如何诱捕猎物的", ["诱捕者"], "敌人/辉萤军势"),
    ("q14", "阿维狄亚的身份是什么", ["准英雄王"], "简介/阿维狄亚"),
    ("q15", "今汐的变奏技能叫什么", ["蟠龙清辉"], "共鸣者/今汐"),
    ("q16", "今汐的特殊料理是什么", ["雪莲酥"], "共鸣者/今汐"),
    ("q17", "凝夜白霜两件套效果是什么", ["冷凝伤害提升10%"], "声骸合鸣/凝夜白霜"),
    ("q18", "中曲台地50%探索报告写了什么", ["瑞狮会馆"], "探索报告/中曲台地"),
    ("q19", "名词注释里的冰棘描述了什么", ["冷凝伤害"], "名词注释/冰棘"),
    ("q20", "道具战地记录6010再出发写了什么", ["新兵"], "道具/战地记录6010：再出发"),
]


def norm(s: str | None) -> str:
    """和 eval.py 的 norm 保持一致（比对口径不同就等于没比对）。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("（", "(").replace("）", ")").replace("：", ":")
    return re.sub(r"\s+", "", s).strip().lower()


def run_probe(cur: sqlite3.Cursor, cfg, name: str, args: dict) -> str | None:
    """按 eval.py 的口径跑一遍探针（只用来回填 expect，不参与评分）。"""
    if name == "entity_attr":
        return cur.execute(
            "SELECT value FROM entity_attrs WHERE title=? AND attr=?",
            (args["title"], args["attr"])).fetchone()
    if name == "entity_json":
        row = cur.execute("SELECT fields_json FROM entities WHERE title=?",
                          (args["title"],)).fetchone()
        if not row or not row[0]:
            return None
        return json.loads(row[0]).get(args["key"])
    if name in ("child", "child_list"):
        allowed = list(child_table_columns(cfg).get(args["table"]) or [])
        allowed += [c for c in ("page_title", "sort", "template", "block")
                    if c not in allowed]
        for k in list(args.get("where") or {}) + [args["column"]]:
            if k not in allowed:
                raise ValueError(f"表 {args['table']} 没有列 {k}（有：{', '.join(allowed)}）")
        sql = f'SELECT "{args["column"]}" FROM {args["table"]} WHERE page_title=?'
        params: list = [args["title"]]
        for k, v in (args.get("where") or {}).items():
            if v is None:      # None 表示"不筛这一列"，占位用
                continue
            sql += f' AND "{k}"=?'
            params.append(v)
        sql += " ORDER BY sort"
        rows = cur.execute(sql, params).fetchall()
        vals = [str(r[0]) for r in rows if r[0] not in (None, "")]
        if name == "child_list":
            return args.get("sep", "；").join(vals) if vals else None
        return vals[0] if vals else None
    raise ValueError(f"未知探针 {name}")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成鸣潮评测题库")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的题库")
    args = ap.parse_args()

    cfg = gamecfg.load_game("mingchao")
    if OUT.exists() and not args.force:
        print(f"题库已存在：{OUT}（--force 覆盖）", file=sys.stderr)
        return 1
    conn = sqlite3.connect(cfg.kb_db)
    cur = conn.cursor()

    # 问答题要回 chunks.jsonl 验关键词，按来源页归组
    page_chunks: dict[str, str] = {}
    with open(cfg.chunks_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            page_chunks[d["source_title"]] = (
                page_chunks.get(d["source_title"], "") + "\n" + d["text"])

    out: list[dict] = []
    errors: list[str] = []

    for qid, q, probe, pargs in TABLE_SPECS:
        row = run_probe(cur, cfg, probe, pargs)
        got = row[0] if isinstance(row, tuple) else row
        if got in (None, ""):
            errors.append(f"{qid} {q}：探针 {probe} 取不到值 {pargs}")
            continue
        out.append({"id": qid, "kind": "table", "q": q, "probe": probe,
                    "args": pargs, "expect": str(got)})

    for qid, q, kws, title in QA_SPECS:
        body = norm(page_chunks.get(title, ""))
        missing = [k for k in kws if norm(k) not in body]
        if missing:
            errors.append(f"{qid} {q}：来源页 {title} 的 chunk 里找不到关键词 {missing}"
                          f"（该页 {len(page_chunks.get(title, ''))} 字）")
            continue
        out.append({"id": qid, "kind": "qa", "q": q, "expect_keywords": kws,
                    "source_title": title})

    if errors:
        print("题库自检未通过：", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    n_table = sum(1 for r in out if r["kind"] == "table")
    n_qa = len(out) - n_table
    print(f"已写入 {OUT}：{len(out)} 题（查表 {n_table}，问答 {n_qa}），自检全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
