#!/usr/bin/env python3
"""最小问答 demo：问题 -> 本地向量检索 -> step-5-preview 组织答案。"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from kblib import embedder as emb
from kblib import retriever

API_BASE = os.environ.get("ANTHROPIC_BASE_URL", "https://api.stepfun.com/step_plan").rstrip("/")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "step-5-preview")


def build_context(chunks):
    parts = []
    for i, c in enumerate(chunks):
        parts.append("--- 资料%d ---\n%s" % (i + 1, c))
    return "\n\n".join(parts)


def generate(question, chunks):
    ctx = build_context(chunks)
    prompt = (
        "你是碧蓝航线知识库问答助手。只根据以下资料回答问题，"
        "不要编造。如果问题本身不成立（比如问某船的二技能，"
        "但资料显示该船只有一个技能），请根据资料如实指出。\n\n"
        + ctx
        + "\n\n问题：" + question + "\n回答："
    )
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 600,
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


def main():
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        print("用法：qa_demo.py <问题>", file=sys.stderr)
        sys.exit(1)
    import sqlite3
    from kblib.config import KB_DB
    conn = sqlite3.connect(KB_DB)
    cur = conn.cursor()
    titles = [r[0] for r in cur.execute("SELECT title FROM ships")]
    # 最长匹配舰船名
    ship = ""
    for t in sorted(titles, key=len, reverse=True):
        if t and t in question:
            ship = t
            break
    table_facts = []
    route = "检索"
    if ship and "技能" in question:
        rows = cur.execute(
            "SELECT slot, name, description FROM skills WHERE ship_title=? ORDER BY slot",
            (ship,),
        ).fetchall()
        if rows:
            route = "查表"
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
    conn.close()
    encoder = emb.get_encoder("onnx")
    store = retriever.get_store("qdrant", dim=encoder.dim)
    vec = encoder.encode_queries([question])[0]
    hits = store.search(vec, top_k=5)
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
