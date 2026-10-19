#!/usr/bin/env python3
"""阶段 4：评测。查表类精确匹配 + 问答类检索命中。

    ./kb/.venv/bin/python eval.py                 # 全量评测（混合检索+rerank）
    ./kb/.venv/bin/python eval.py --kind table    # 只评测查表类
    ./kb/.venv/bin/python eval.py --kind qa --retrieval vector   # 退回 v1 纯向量口径
    ./kb/.venv/bin/python eval.py -v              # 打印每条明细

题库在 eval/questions.jsonl（随代码提交）。每条：
  {"id","kind":"table","q":"问题","probe":"skill_name",
   "args":{"title":"光辉","slot":2},"expect":"燃烧号令"}
  {"id","kind":"qa","q":"问题","expect_keywords":["烟雾弹","轻巡"],
   "source_title":"贝尔法斯特"}

判分：
  * table —— 探针查 kb.db，和 expect 归一化后精确比对
  * qa    —— 检索 top_k，关键词全部命中才算过（考检索+切分，不考生成）。
             v2 默认走"向量+BM25 RRF 融合初排 → cross-encoder rerank"，
             --retrieval vector 可切回 v1 的纯向量口径做同题对比。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

from kblib import retriever
from kblib.config import CHUNKS_PATH, KB_DB, QUESTIONS_PATH

log = logging.getLogger("kb.eval")


# ---------------------------------------------------------------- 归一化
def norm(s: str | None) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("（", "(").replace("）", ")").replace("：", ":")
    s = re.sub(r"\s+", "", s)
    return s.strip().lower()


# ---------------------------------------------------------------- 查表探针
def probe_skill_name(cur, title: str, slot: int, variant: str = "") -> str | None:
    row = cur.execute(
        "SELECT name FROM skills WHERE ship_title=? AND slot=? AND variant=?",
        (title, slot, variant),
    ).fetchone()
    return row[0] if row else None


def probe_skill_desc(cur, title: str, slot: int, variant: str = "") -> str | None:
    row = cur.execute(
        "SELECT description FROM skills WHERE ship_title=? AND slot=? AND variant=?",
        (title, slot, variant),
    ).fetchone()
    return row[0] if row else None


def probe_stat(cur, title: str, stat: str, which: str = "max") -> str | None:
    col = "max_value" if which == "max" else "initial_value"
    row = cur.execute(
        f"SELECT {col} FROM ship_stats WHERE title=? AND stat=?", (title, stat)
    ).fetchone()
    return row[0] if row else None


def probe_ship_field(cur, title: str, field: str) -> str | None:
    allowed = {"name", "model", "name_en", "ship_type", "rarity", "faction",
               "hull_type", "speed", "luck", "build_time", "stars", "code"}
    if field not in allowed:
        raise ValueError(f"不允许的字段：{field}")
    row = cur.execute(f"SELECT {field} FROM ships WHERE title=?", (title,)).fetchone()
    return row[0] if row else None


def probe_equip_field(cur, title: str, field: str) -> str | None:
    allowed = {"name", "category", "equip_type", "trait", "rarity", "faction",
               "damage", "rof", "notes"}
    if field not in allowed:
        raise ValueError(f"不允许的字段：{field}")
    row = cur.execute(f"SELECT {field} FROM equipments WHERE title=?", (title,)).fetchone()
    return row[0] if row else None


def probe_slot_equip(cur, title: str, slot: int) -> str | None:
    row = cur.execute(
        "SELECT equip_name FROM ship_slots WHERE ship_title=? AND slot=?", (title, slot)
    ).fetchone()
    return row[0] if row else None


def probe_equip_json(cur, title: str, key: str) -> str | None:
    row = cur.execute("SELECT fields_json FROM equipments WHERE title=?", (title,)).fetchone()
    if not row or not row[0]:
        return None
    return json.loads(row[0]).get(key)


def probe_ship_json(cur, title: str, key: str) -> str | None:
    row = cur.execute("SELECT fields_json FROM ships WHERE title=?", (title,)).fetchone()
    if not row or not row[0]:
        return None
    return json.loads(row[0]).get(key)


PROBES = {
    "skill_name": probe_skill_name,
    "skill_desc": probe_skill_desc,
    "stat": probe_stat,
    "ship_field": probe_ship_field,
    "equip_field": probe_equip_field,
    "slot_equip": probe_slot_equip,
    "equip_json": probe_equip_json,
    "ship_json": probe_ship_json,
}


# ---------------------------------------------------------------- 评测
def run_table(cur, q: dict) -> tuple[bool, str]:
    fn = PROBES[q["probe"]]
    got = fn(cur, **q["args"])
    exp = q["expect"]
    ok = norm(got) == norm(exp)
    return ok, f"期望 {exp!r} / 实得 {got!r}"


def run_qa(store, q: dict, top_k: int = 3) -> tuple[bool, str, list[retriever.Hit]]:
    vec = store_encoder.encode_queries([q["q"]])[0]
    # 混合检索要问题原文（BM25 分词 + rerank 都要）；纯向量库不需要
    kw = {"query": q["q"]} if isinstance(store, retriever.HybridStore) else {}
    hits = store.search(vec, top_k=top_k, **kw)
    ctx = "\n".join(h.text for h in hits)
    kws = q["expect_keywords"]
    missing = [k for k in kws if norm(k) not in norm(ctx)]
    ok = not missing and bool(hits)
    route = "混合+rerank" if isinstance(store, retriever.HybridStore) else "纯向量"
    detail = (f"top{len(hits)} 全命中（{route}）" if not missing
              else f"top{len(hits)} 命中，缺关键词 {missing}（{route}）")
    return ok, detail, hits


def load_questions(path: Path, kind: str | None) -> list[dict]:
    qs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if kind:
        qs = [q for q in qs if q["kind"] == kind]
    return qs


def main() -> int:
    global store_encoder
    ap = argparse.ArgumentParser(description="知识库评测")
    ap.add_argument("--db", type=Path, default=KB_DB)
    ap.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    ap.add_argument("--kind", choices=["table", "qa"], default=None)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--retrieval", default="hybrid", choices=["hybrid", "vector"],
                    help="qa 用哪条检索链路；vector 是 v1 纯向量口径，留着做同题对比")
    ap.add_argument("--recall-k", type=int, default=20, help="混合检索每路召回条数 / 精排候选数")
    ap.add_argument("--backend", default="auto", choices=["auto", "torch", "onnx"])
    ap.add_argument("--store", default="qdrant", choices=["qdrant", "numpy"])
    ap.add_argument("--out", type=Path, default=None, help="明细 JSON 输出路径")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not args.questions.exists():
        print(f"找不到题库 {args.questions}", file=sys.stderr)
        return 1

    questions = load_questions(args.questions, args.kind)
    if not questions:
        print("题库为空", file=sys.stderr)
        return 1

    store = None
    if any(q["kind"] == "qa" for q in questions):
        from kblib import embedder as emb

        store_encoder = emb.get_encoder(args.backend)
        if args.retrieval == "hybrid":
            store = retriever.get_hybrid_store(
                args.store, dim=store_encoder.dim, recall_k=args.recall_k)
        else:
            store = retriever.get_store(args.store, dim=store_encoder.dim)
        if store.count() == 0:
            print("向量库是空的，先跑 embed_index.py", file=sys.stderr)
            return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    results = []
    passed = {"table": 0, "qa": 0}
    total = {"table": 0, "qa": 0}
    print(f"共 {len(questions)} 题（查表 {sum(1 for q in questions if q['kind']=='table')}，"
          f"问答 {sum(1 for q in questions if q['kind']=='qa')}）\n")

    for q in questions:
        total[q["kind"]] += 1
        if q["kind"] == "table":
            try:
                ok, detail = run_table(cur, q)
                hits = []
            except Exception as e:
                ok, detail, hits = False, f"探针异常：{e}", []
        else:
            ok, detail, hits = run_qa(store, q, args.top_k)
        passed[q["kind"]] += 1 if ok else 0
        mark = "✓" if ok else "✗"
        print(f"{mark} [{q['kind']}] {q['id']} {q['q']}")
        if not ok or args.verbose:
            print(f"    {detail}")
            for h in hits[:3]:
                print(f"    · {h.score:.3f} {h.source_title}｜{h.heading}｜{h.text[:70]}")
        results.append({"id": q["id"], "kind": q["kind"], "q": q["q"],
                        "pass": ok, "detail": detail})

    n_pass = sum(passed.values())
    n_total = sum(total.values())
    print("\n" + "=" * 56)
    for kind in ("table", "qa"):
        if total[kind]:
            print(f"{kind:6} 通过 {passed[kind]}/{total[kind]}（{passed[kind]/total[kind]:.0%}）")
    print(f"总通过率 {n_pass}/{n_total} = {n_pass/max(n_total,1):.1%}")

    if args.out:
        args.out.write_text(json.dumps(
            {"total": n_total, "passed": n_pass, "rate": round(n_pass / max(n_total, 1), 4),
             "by_kind": {k: {"passed": passed[k], "total": total[k]} for k in total},
             "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细 → {args.out}")

    conn.close()
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
