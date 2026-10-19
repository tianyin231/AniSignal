#!/usr/bin/env python3
"""最小问答 demo：问题 -> 查表 + 本地向量检索 -> step-5-preview 组织答案。

    ./kb/.venv/bin/python qa_demo.py 阿米娅二技能叫什么
    ./kb/.venv/bin/python qa_demo.py --game arknights 塞雷娅天赋

--game 换游戏配置（kb/games/<game>.yaml）：碧蓝航线走 ships/skills 专用表，
其余游戏走 entities/entity_attrs + 配置声明的子表，两边都是"先查表再检索"。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request

from kblib import embedder as emb
from kblib import gamecfg, retriever
from kblib.extractors import child_table_columns

API_BASE = os.environ.get("ANTHROPIC_BASE_URL", "https://api.stepfun.com/step_plan").rstrip("/")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "step-5-preview")


def build_context(chunks):
    parts = []
    for i, c in enumerate(chunks):
        parts.append("--- 资料%d ---\n%s" % (i + 1, c))
    return "\n\n".join(parts)


CFG = None  # main() 里填，generate() 拿去拼 system 提示词


def generate(question, chunks):
    ctx = build_context(chunks)
    prompt = (
        f"你是{CFG.label}知识库问答助手。只根据以下资料回答问题，"
        "不要编造。如果问题本身不成立（比如问某船的二技能，"
        "但资料显示该船只有一个技能），请根据资料如实指出。\n\n"
        + ctx
        + "\n\n问题：" + question + "\n回答："
    )
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        API_BASE + "/v1/messages",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    texts = []
    for b in d.get("content", []):
        texts.append(b.get("text", ""))
    return "".join(texts)


def facts_azurlane(cur, ship, question):
    """碧蓝航线：ships/skills 表（专用抽取器落的那几张）。"""
    table_facts = []
    rows = cur.execute(
        "SELECT slot, name, description FROM skills WHERE ship_title=? ORDER BY slot",
        (ship,),
    ).fetchall()
    if rows:
        for slot, name, desc in rows:
            table_facts.append(
                "%s 的技能%d：%s —— %s" % (ship, slot, name, (desc or "")[:200])
            )
        # 问的是第几个技能？
        want = 0
        for ch, n in (("一", 1), ("二", 2), ("三", 3), ("四", 4)):
            if ch + "技能" in question:
                want = n
        slots = [r[0] for r in rows]
        if want and want not in slots:
            table_facts.append(
                "注：资料中%s一共只有%d个技能（%s），没有技能%d。"
                % (ship, len(slots), "、".join("技能%d" % s for s in slots), want)
            )
    return table_facts


def facts_generic(cur, cfg, title, question):
    """通用实体路：entities/entity_attrs + 配置声明的子表，全游戏一套代码。"""
    facts = []
    row = cur.execute(
        "SELECT kind, name, category FROM entities WHERE title=?", (title,)
    ).fetchone()
    if row:
        facts.append("%s：%s（%s）" % (title, row[1] or row[0], row[2] or row[0]))
    for attr, value in cur.execute(
        "SELECT attr, value FROM entity_attrs WHERE title=? ORDER BY attr", (title,)
    ):
        facts.append("%s：%s" % (attr, (value or "")[:200]))
    n_rows = 0
    for table, cols in child_table_columns(cfg).items():
        for r in cur.execute(
            f'SELECT * FROM "{table}" WHERE page_title=? ORDER BY sort', (title,)
        ):
            if n_rows >= 40:
                break
            n_rows += 1
            facts.append("%s｜%s" % (
                table, "｜".join(f"{c}={v}" for c, v in zip(r.keys(), tuple(r))
                                if v not in (None, "") and c != "page_title")))
    return facts


def main():
    global CFG
    ap = argparse.ArgumentParser(description="知识库最小问答 demo")
    ap.add_argument("--game", default=gamecfg.DEFAULT_GAME,
                    help=f"游戏配置名，默认 {gamecfg.DEFAULT_GAME}")
    ap.add_argument("question", nargs="+")
    args = ap.parse_args()
    question = " ".join(args.question).strip()
    CFG = gamecfg.load_game(args.game)

    conn = sqlite3.connect(CFG.kb_db)
    # facts_generic 按列名拼子表事实（zip(r.keys(), r)），没有 Row 工厂拿到的是裸 tuple，
    # 一进通用实体路就 AttributeError——碧蓝航线走 ships 专用表不经过那儿，所以一直没暴露
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if CFG.extractor_module:
        # 碧蓝航线：舰娘页标题（专用抽取器落的 ships 表）
        cand = {r[0]: r[0] for r in cur.execute("SELECT title FROM ships")}
    else:
        # 通用实体路：实体页标题 + 重定向别名。别名也必须收——问法里常出现页标题以外
        # 的叫法（"Justice Knight"），命中后要归到目标页，不然 facts_generic 拿别名
        # 去查 entities 一条都没有。（aliases 是 CORE_SCHEMA 的表，没抓重定向就是空表）
        cand = {r[0]: r[0] for r in cur.execute("SELECT title FROM entities")}
        for alias, target in cur.execute("SELECT alias, target FROM aliases"):
            cand.setdefault(alias, target)
    # 最长匹配实体名（舰船/干员/敌人…）；hit 统一落到 entities/ships 的标题上
    hit = ""
    for t, target in sorted(cand.items(), key=lambda kv: -len(kv[0])):
        if t and t in question:
            hit = target
            break
    table_facts = []
    route = "检索"
    if hit and CFG.extractor_module and "技能" in question:
        table_facts = facts_azurlane(cur, hit, question)
        route = "查表" if table_facts else "检索"
    elif hit:
        table_facts = facts_generic(cur, CFG, hit, question)
        route = "查表" if table_facts else "检索"
    conn.close()
    encoder = emb.get_encoder("onnx")
    store = retriever.get_hybrid_store("qdrant", dim=encoder.dim, game=CFG.name)
    vec = encoder.encode_queries([question])[0]
    hits = store.search(vec, top_k=5, query=question)
    print("== 路由：%s ==" % route)
    print("== 检索命中 ==")
    for h in hits:
        print("  [%.3f] %s | %s" % (h.score, h.source_title, h.heading))
    print()
    print("== 回答 ==")
    if table_facts:
        print(generate(question, table_facts + [h.text for h in hits[:2]]))
    else:
        print(generate(question, [h.text for h in hits]))
    print()
    print("== 来源 ==")
    for h in hits:
        print("  - " + h.source_title)


if __name__ == "__main__":
    main()
