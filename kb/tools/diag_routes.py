#!/usr/bin/env python3
"""检索短板诊断：对问答题逐路打印 BM25 / 向量 / RRF / rerank 的排名。

eval.py 只告诉你"哪题挂了、缺哪个关键词"，不告诉你挂在链路的哪一段。
这个工具把同一道题在三路里的 top-N 都摊开，并标出**含全部期望关键词的
chunk**（判分意义上的等价金标，不用手抄 chunk_id）：

  * 等价金标在向量路排名高、BM25 路摸不到 → 专有名词/同义词问题；
  * 两路都排得到、rerank 后掉出去 → 精排问题，换 reranker 或加负样本；
  * 三路都排不到 → 召回问题：chunk 切坏了，或库里根本没有这块内容
    （星铁新一代角色的技能整块塞在 infobox 里被 exclude 挖走，就属于这种，
    等价金标压根不存在，只能补 chunk，不是调检索能解决的）。

用法：
  ./kb/.venv/bin/python tools/diag_routes.py --game starrail
  ./kb/.venv/bin/python tools/diag_routes.py --game starrail --ids q02,q06
  ./kb/.venv/bin/python tools/diag_routes.py --game starrail --only-fail
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kblib import embedder as emb  # noqa: E402
from kblib import gamecfg, retriever  # noqa: E402


def norm(s: str | None) -> str:
    """和 eval.py 的判分归一化保持一致，否则"等价金标"会认错。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("（", "(").replace("）", ")").replace("：", ":")
    return re.sub(r"\s+", "", s).strip().lower()


def is_gold(payload: dict, kws: list[str]) -> bool:
    """chunk 文本是否含全部期望关键词（= 判分能过）。"""
    text = norm(payload.get("text", ""))
    return bool(text) and all(norm(k) in text for k in kws)


def show(rank: int, cid: str, score: float, payload: dict, gold: bool) -> str:
    mark = "  <<< 等价金标" if gold else ""
    return (f"  {rank:2}. {score:+.3f} {cid} "
            f"{payload.get('source_title')}｜{payload.get('kind')}{mark}")


def main() -> int:
    ap = argparse.ArgumentParser(description="逐路诊断问答检索")
    ap.add_argument("--game", default=gamecfg.DEFAULT_GAME)
    ap.add_argument("--ids", default=None, help="逗号分隔的题号，默认全部问答")
    ap.add_argument("--only-fail", action="store_true",
                    help="只诊断挂了/险过的题（top-k 里没有等价金标的）")
    ap.add_argument("--top-n", type=int, default=6, help="每路打印前几条")
    ap.add_argument("--recall-k", type=int, default=20)
    args = ap.parse_args()

    cfg = gamecfg.load_game(args.game)
    qs = [json.loads(l) for l in
          cfg.questions_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    qs = [q for q in qs if q["kind"] == "qa"]
    if args.ids:
        want = {s.strip() for s in args.ids.split(",")}
        qs = [q for q in qs if q["id"] in want]

    enc = emb.get_encoder("auto")
    store = retriever.get_hybrid_store("qdrant", dim=enc.dim,
                                       recall_k=args.recall_k, game=cfg.name)
    print(f"bm25 docs={len(store.bm25)} vec={store.vector.count()} "
          f"reranker={'有' if store.reranker else '无'}\n")

    for q in qs:
        query, kws = q["q"], q.get("expect_keywords", [])
        vec = enc.encode_queries([query])[0]
        vh = store.vector.search(vec, top_k=args.recall_k)
        bh = store.bm25.search(query, top_k=args.recall_k)
        pl = store.payloads

        gv = next((i for i, h in enumerate(vh, 1)
                   if is_gold(pl.get(h.chunk_id, {}), kws)), None)
        gb = next((i for i, (cid, _) in enumerate(bh, 1)
                   if is_gold(pl.get(cid, {}), kws)), None)

        # RRF 融合（和 HybridStore.search 同一套权重）
        fused: dict[str, float] = {}
        for rank, h in enumerate(vh, 1):
            fused[h.chunk_id] = fused.get(h.chunk_id, 0) + 1 / (store.rrf_k + rank)
        for rank, (cid, _) in enumerate(bh, 1):
            fused[cid] = fused.get(cid, 0) + 1 / (store.rrf_k + rank)
        order = sorted(fused.items(), key=lambda kv: -kv[1])[:args.recall_k]
        gf = next((i for i, (cid, _) in enumerate(order, 1)
                   if is_gold(pl.get(cid, {}), kws)), None)

        if args.only_fail and gf is not None and gf <= 3:
            continue
        print(f"===== {q['id']} {query}  期望关键词 {kws}")
        print("-- 向量路 --")
        for i, h in enumerate(vh[:args.top_n], 1):
            print(show(i, h.chunk_id, h.score, pl.get(h.chunk_id, {}),
                       is_gold(pl.get(h.chunk_id, {}), kws)))
        print("-- BM25 路 --")
        for i, (cid, s) in enumerate(bh[:args.top_n], 1):
            print(show(i, cid, s, pl.get(cid, {}), is_gold(pl.get(cid, {}), kws)))
        print(f"  等价金标排名：向量路 {gv} / BM25 路 {gb} / 融合 top{args.recall_k} {gf}")
        if gf and store.reranker:
            scores = store.reranker.score([(query, pl[cid].get("text", ""))
                                           for cid, _ in order])
            ranked = sorted(zip([c for c, _ in order], scores), key=lambda kv: -kv[1])
            gr = next((i for i, (cid, _) in enumerate(ranked, 1)
                       if is_gold(pl.get(cid, {}), kws)), None)
            print(f"  rerank 后等价金标排名：{gr}")
            for i, (cid, s) in enumerate(ranked[:args.top_n], 1):
                print(show(i, cid, s, pl.get(cid, {}),
                           is_gold(pl.get(cid, {}), kws)))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
