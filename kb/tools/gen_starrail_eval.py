#!/usr/bin/env python3
"""生成星铁评测题库（50 题：30 查表 + 20 问答）。

题库要"期望值和库里一字不差"，手抄必错（技能名带 •、`%`、换行…）。所以这里
每条 expect 都现场从 kb.db 查出来，只把"问法"写死。改了 parse 重跑本脚本即可。

    ./kb/.venv/bin/python tools/gen_starrail_eval.py            # 写进 eval/starrail/
    ./kb/.venv/bin/python tools/gen_starrail_eval.py --check    # 只校验不落盘
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT = Path(__file__).resolve().parents[1] / "eval" / "starrail" / "questions.jsonl"

# 方舟口径是 30 查表 + 20 问答，星铁对齐。这里多写了几条候选，按这个集合裁剪，
# 优先级：每个实体族至少 1 条、child_list/child/entity_attr 三种探针都覆盖到。
KEEP_TABLE = ["t01", "t02", "t03", "t04", "t05", "t06", "t07", "t08", "t09", "t10",
              "t11", "t12", "t14", "t15", "t16", "t17", "t18", "t19", "t21", "t22",
              "t23", "t25", "t27", "t28", "t29", "t30", "t33", "t35", "t36", "t32"]


def q(cur: sqlite3.Cursor, sql: str, args: tuple, probe: str = "") -> str | None:
    """取期望值。child_list 要的是全表按 sort 串起来的一整串（和 eval 的探针同口径），
    只取第一行会和 eval 实得差一截。"""
    if probe == "child_list":
        return qlist(cur, sql, args)
    row = cur.execute(sql, args).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def qlist(cur: sqlite3.Cursor, sql: str, args: tuple, sep: str = "；") -> str | None:
    vals = [str(r[0]) for r in cur.execute(sql, args).fetchall() if r[0] not in (None, "")]
    return sep.join(vals) if vals else None


# (id, 问题, probe, args, 取期望值的 SQL)
TABLE: list[tuple] = [
    # ---- 角色：entity_attr ----
    ("t01", "姬子的命途是什么", "entity_attr", {"title": "姬子", "attr": "命途"},
     "SELECT value FROM entity_attrs WHERE title='姬子' AND attr='命途'", ()),
    ("t02", "姬子的元素属性是什么", "entity_attr", {"title": "姬子", "attr": "元素属性"},
     "SELECT value FROM entity_attrs WHERE title='姬子' AND attr='元素属性'", ()),
    ("t03", "姬子的稀有度是几星", "entity_attr", {"title": "姬子", "attr": "稀有度"},
     "SELECT value FROM entity_attrs WHERE title='姬子' AND attr='稀有度'", ()),
    ("t04", "姬子属于哪个阵营", "entity_attr", {"title": "姬子", "attr": "阵营"},
     "SELECT value FROM entity_attrs WHERE title='姬子' AND attr='阵营'", ()),
    ("t05", "姬子是哪个版本实装的", "entity_attr", {"title": "姬子", "attr": "实装版本"},
     "SELECT value FROM entity_attrs WHERE title='姬子' AND attr='实装版本'", ()),
    ("t06", "姬子的中文配音演员是谁", "entity_attr", {"title": "姬子", "attr": "中文CV"},
     "SELECT value FROM entity_attrs WHERE title='姬子' AND attr='中文CV'", ()),
    # ---- 角色：技能 / 星魂 / 额外能力 / 等级数值 ----
    ("t07", "卡芙卡的战技叫什么名字", "child",
     {"table": "sr_character_skills", "title": "卡芙卡", "column": "name",
      "where": {"slot": "战技"}},
     "SELECT name FROM sr_character_skills WHERE page_title='卡芙卡' AND slot='战技'"
     " ORDER BY sort", ()),
    ("t08", "姬子的终结技叫什么名字", "child",
     {"table": "sr_character_skills", "title": "姬子", "column": "name",
      "where": {"slot": "终结技"}},
     "SELECT name FROM sr_character_skills WHERE page_title='姬子' AND slot='终结技'"
     " ORDER BY sort", ()),
    ("t09", "姬子有哪些技能", "child_list",
     {"table": "sr_character_skills", "title": "姬子", "column": "name"},
     "SELECT name FROM sr_character_skills WHERE page_title='姬子' ORDER BY sort", ()),
    ("t10", "姬子的第三个星魂叫什么", "child",
     {"table": "sr_character_eidolons", "title": "姬子", "column": "name",
      "where": {"index": "3"}},
     "SELECT name FROM sr_character_eidolons WHERE page_title='姬子' AND \"index\"='3'"
     " ORDER BY sort", ()),
    ("t11", "姬子有哪些额外能力", "child_list",
     {"table": "sr_character_traces", "title": "姬子", "column": "name"},
     "SELECT name FROM sr_character_traces WHERE page_title='姬子' ORDER BY sort", ()),
    ("t12", "姬子战技1级的伤害倍率是多少", "child",
     {"table": "sr_character_skill_levels", "title": "姬子", "column": "value",
      "where": {"slot": "战技", "level": "1"}},
     "SELECT value FROM sr_character_skill_levels WHERE page_title='姬子'"
     " AND slot='战技' AND level='1' ORDER BY sort", ()),
    ("t13", "三月七•存护的第二个星魂有什么效果", "child",
     {"table": "sr_character_eidolons", "title": "三月七•存护", "column": "effect",
      "where": {"index": "2"}},
     "SELECT effect FROM sr_character_eidolons WHERE page_title='三月七•存护'"
     " AND \"index\"='2' ORDER BY sort", ()),
    ("t14", "丹恒的普攻描述是什么", "child",
     {"table": "sr_character_skills", "title": "丹恒", "column": "description",
      "where": {"slot": "普攻"}},
     "SELECT description FROM sr_character_skills WHERE page_title='丹恒'"
     " AND slot='普攻' ORDER BY sort", ()),
    # ---- 光锥 ----
    ("t15", "光锥「我」的诞生的命途是什么", "entity_attr",
     {"title": "「我」的诞生", "attr": "命途"},
     "SELECT value FROM entity_attrs WHERE title='「我」的诞生' AND attr='命途'", ()),
    ("t16", "光锥「我」的诞生的技能叫什么", "entity_attr",
     {"title": "「我」的诞生", "attr": "技能名称"},
     "SELECT value FROM entity_attrs WHERE title='「我」的诞生' AND attr='技能名称'", ()),
    # ---- 敌人 ----
    ("t17", "蚕食者之影是什么类型的敌人", "entity_attr",
     {"title": "蚕食者之影", "attr": "类型"},
     "SELECT value FROM entity_attrs WHERE title='蚕食者之影' AND attr='类型'", ()),
    ("t18", "蚕食者之影的弱点是什么", "entity_attr",
     {"title": "蚕食者之影", "attr": "弱点"},
     "SELECT value FROM entity_attrs WHERE title='蚕食者之影' AND attr='弱点'", ()),
    ("t19", "蚕食者之影的第二个技能叫什么", "child",
     {"table": "sr_enemy_skills", "title": "蚕食者之影", "column": "name",
      "where": {"index": "2"}},
     "SELECT name FROM sr_enemy_skills WHERE page_title='蚕食者之影' AND \"index\"='2'"
     " ORDER BY sort", ()),
    ("t20", "蚕食者之影有哪些技能", "child_list",
     {"table": "sr_enemy_skills", "title": "蚕食者之影", "column": "name"},
     "SELECT name FROM sr_enemy_skills WHERE page_title='蚕食者之影' ORDER BY sort", ()),
    # ---- 遗器 / 材料 / 关卡 ----
    ("t21", "遗器不老者的仙舟属于什么类别", "entity_attr",
     {"title": "不老者的仙舟", "attr": "类别"},
     "SELECT value FROM entity_attrs WHERE title='不老者的仙舟' AND attr='类别'", ()),
    ("t22", "遗器停转的萨尔索图的连结绳叫什么", "entity_attr",
     {"title": "停转的萨尔索图", "attr": "连结绳"},
     "SELECT value FROM entity_attrs WHERE title='停转的萨尔索图' AND attr='连结绳'", ()),
    ("t23", "材料「同谐」之影的稀有度是几星", "entity_attr",
     {"title": "「同谐」之影", "attr": "稀有度"},
     "SELECT value FROM entity_attrs WHERE title='「同谐」之影' AND attr='稀有度'", ()),
    ("t24", "材料「同谐」之影怎么获取", "entity_attr",
     {"title": "「同谐」之影", "attr": "获取方式"},
     "SELECT value FROM entity_attrs WHERE title='「同谐」之影' AND attr='获取方式'", ()),
    ("t25", "关卡丰饶之蕾 • 绥园消耗多少开拓力", "entity_attr",
     {"title": "丰饶之蕾 • 绥园", "attr": "开拓力消耗"},
     "SELECT value FROM entity_attrs WHERE title='丰饶之蕾 • 绥园' AND attr='开拓力消耗'", ()),
    ("t26", "关卡丰饶之蕾 • 绥园建议用什么属性打", "entity_attr",
     {"title": "丰饶之蕾 • 绥园", "attr": "建议属性"},
     "SELECT value FROM entity_attrs WHERE title='丰饶之蕾 • 绥园' AND attr='建议属性'", ()),
    # ---- 模拟宇宙 / 差分宇宙 / 组件 ----
    ("t27", "差分宇宙方程「一首美丽的诗」的稀有度是几星", "entity_attr",
     {"title": "一首美丽的诗", "attr": "稀有度"},
     "SELECT value FROM entity_attrs WHERE title='一首美丽的诗' AND attr='稀有度'", ()),
    ("t28", "差分宇宙方程「一首美丽的诗」的命途1需求是多少", "entity_attr",
     {"title": "一首美丽的诗", "attr": "命途1需求"},
     "SELECT value FROM entity_attrs WHERE title='一首美丽的诗' AND attr='命途1需求'", ()),
    ("t29", "差分宇宙祝福 BCI-34型灰质的命途是什么", "entity_attr",
     {"title": "BCI-34型灰质", "attr": "命途"},
     "SELECT value FROM entity_attrs WHERE title='BCI-34型灰质' AND attr='命途'", ()),
    ("t30", "差分宇宙组件不老心的特性是什么", "entity_attr",
     {"title": "不老心", "attr": "特性"},
     "SELECT value FROM entity_attrs WHERE title='不老心' AND attr='特性'", ()),
]

# 语音 / 如我所书这两类没有 entity_attrs，单独走 child 探针
EXTRA_TABLE: list[tuple] = [
    ("t31", "三月七的第一条语音标题是什么", "child",
     {"table": "sr_voice_lines", "title": "三月七/语音", "column": "voice_type",
      "where": {"sort": "1"}},
     "SELECT voice_type FROM sr_voice_lines WHERE page_title='三月七/语音' AND sort='1'", ()),
    ("t32", "三月七语音里「问候」说了什么", "child",
     {"table": "sr_voice_lines", "title": "三月七/语音", "column": "content",
      "where": {"voice_type": "问候"}},
     "SELECT content FROM sr_voice_lines WHERE page_title='三月七/语音'"
     " AND voice_type='问候' ORDER BY sort", ()),
    ("t33", "姬子语音里「道别」说了什么", "child",
     {"table": "sr_voice_lines", "title": "姬子/语音", "column": "content",
      "where": {"voice_type": "道别"}},
     "SELECT content FROM sr_voice_lines WHERE page_title='姬子/语音'"
     " AND voice_type='道别' ORDER BY sort", ()),
    ("t34", "万敌的如我所书第一章第一页的页码是什么", "child",
     {"table": "sr_book_pages", "title": "万敌/如我所书", "column": "page",
      "where": {"chapter": "1", "index": "1"}},
     "SELECT page FROM sr_book_pages WHERE page_title='万敌/如我所书'"
     " AND chapter='1' AND \"index\"='1' ORDER BY sort", ()),
    ("t35", "万敌的如我所书第一章第一页左页写了什么", "child",
     {"table": "sr_book_pages", "title": "万敌/如我所书", "column": "left",
      "where": {"chapter": "1", "index": "1"}},
     "SELECT left FROM sr_book_pages WHERE page_title='万敌/如我所书'"
     " AND chapter='1' AND \"index\"='1' ORDER BY sort", ()),
    ("t36", "姬子战技10级的伤害倍率是多少", "child",
     {"table": "sr_character_skill_levels", "title": "姬子", "column": "value",
      "where": {"slot": "战技", "level": "10", "prop": "1"}},
     "SELECT value FROM sr_character_skill_levels WHERE page_title='姬子'"
     " AND slot='战技' AND level='10' AND prop='1' ORDER BY sort", ()),
    ("t37", "卡芙卡天赋的追加攻击伤害1级数值是多少", "child",
     {"table": "sr_character_skill_attrs", "title": "卡芙卡", "column": "value",
      "where": {"name": "追加攻击伤害", "block": "4"}},
     "SELECT value FROM sr_character_skill_attrs WHERE page_title='卡芙卡'"
     " AND name='追加攻击伤害' AND block='4' ORDER BY sort", ()),
]

# 问答类：只考"检索+切分"，关键词必须落在 top-k 的 chunk 文本里
QA: list[tuple] = [
    ("q01", "姬子的角色定位是什么", ["智识命途", "火属性"], "姬子"),
    ("q02", "姬子的角色故事1讲了什么", ["星际航行动力学"], "姬子"),
    ("q03", "卡芙卡的终结技悲剧尽头的颤音有什么效果", ["触电"], "卡芙卡"),
    ("q04", "白厄的终结技永劫燔世，其将背负有什么效果", ["变身"], "白厄"),
    ("q05", "流萤是使用什么装甲战斗的", ["萨姆"], "流萤"),
    ("q06", "镜流的战技叫什么名字", ["无罅飞光"], "镜流"),
    ("q07", "光锥「我」的诞生的技能效果是什么", ["追加攻击"], "「我」的诞生"),
    ("q08", "光锥「行于流逝的岸」的稀有度和命途", ["虚无"], "行于流逝的岸"),
    ("q09", "遗器不老者的仙舟的两件套效果是什么", ["生命上限"], "不老者的仙舟"),
    ("q10", "遗器停转的萨尔索图的两件套效果是什么", ["暴击率"], "停转的萨尔索图"),
    ("q11", "蚕食者之影的技能流年谴罚有什么效果", ["虚数属性伤害"], "蚕食者之影"),
    ("q12", "星期日的终结技叫什么名字", ["轻与伤痕的赞颂"], "星期日"),
    ("q13", "三月七的初次见面语音说了什么", ["入职"], "三月七/语音"),
    ("q14", "万敌的如我所书里写了关于迈德漠斯的什么", ["悬锋"], "万敌/如我所书"),
    ("q15", "差分宇宙方程「一首美丽的诗」的演绎内容是什么", ["美丽的诗"], "一首美丽的诗"),
    ("q16", "成就「虎克的礼物」怎么完成", ["同行任务"], "与你同行的回忆"),
    ("q17", "丹恒•腾荒的配装推荐里毕业光锥是什么", ["纵然山河万程"], "丹恒•腾荒/攻略"),
    ("q18", "星穹列车的领航员是谁", ["姬子"], "姬子"),
    ("q19", "仙舟「罗浮」的云骑军是什么", ["云骑"], "仙舟「罗浮」"),
    ("q20", "模拟宇宙里奇物「奇思狂想」机器团的效果", ["奇思狂想"], "「奇思狂想」机器团"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path,
                    default=Path(__file__).resolve().parents[1] / "data" / "starrail" / "kb.db")
    ap.add_argument("--chunks", type=Path,
                    default=Path(__file__).resolve().parents[1] / "data" / "starrail" / "chunks.jsonl")
    ap.add_argument("--check", action="store_true", help="只校验不落盘")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    rows: list[dict] = []
    bad: list[str] = []

    for tid, question, probe, pargs, sql, sqlargs in TABLE + EXTRA_TABLE:
        if tid not in KEEP_TABLE:
            continue
        expect = q(cur, sql, sqlargs, probe)
        if not expect:
            bad.append(f"{tid} 查不到期望值：{question}")
            continue
        rows.append({"id": tid, "kind": "table", "q": question,
                     "probe": probe, "args": pargs, "expect": expect})

    # 问答类先自检：关键词必须真的出现在该页切出来的 chunk 里，否则题目再对也考不着
    page_text: dict[str, str] = {}
    with open(args.chunks, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            page_text[d["source_title"]] = page_text.get(d["source_title"], "") + "\n" + d["text"]
    for qid, question, kws, src in QA:
        text = page_text.get(src)
        if text is None:
            bad.append(f"{qid} 来源页 {src} 没有任何 chunk")
            continue
        miss = [k for k in kws if k not in text]
        if miss:
            bad.append(f"{qid} 关键词 {miss} 不在 {src} 的 chunk 里")
            continue
        rows.append({"id": qid, "kind": "qa", "q": question,
                     "expect_keywords": kws, "source_title": src})

    print(f"查表 {sum(1 for r in rows if r['kind']=='table')} 题，"
          f"问答 {sum(1 for r in rows if r['kind']=='qa')} 题")
    for b in bad:
        print("  ✗", b)
    if bad:
        return 1
    if args.check:
        for r in rows:
            print(f"  {r['id']} {r['q']} → {r.get('expect', r.get('expect_keywords'))}")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")
    print(f"→ {OUT}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
