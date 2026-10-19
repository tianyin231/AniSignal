#!/usr/bin/env python3
"""增量对账体检：chunks.jsonl vs qdrant 库里已有向量。

embed_index.py --append 之前先跑这个，回答三个问题：
  1. 库里已有的 chunk_id 有多少文本没变（→ 跳过，不重算向量）；
  2. 有多少文本变了（→ 原地覆盖，要重算）；
  3. chunks.jsonl 里有、库里没有的（→ 新点，要新算）。
另外按长度列出"库里缺失的最长 chunk"，即审计报告第 6 节那批尾部没 flush 的。

用法：./kb/.venv/bin/python tools/check_append.py [--game arknights] [--chunks ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib import gamecfg, qdrant_server, retriever  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="chunks.jsonl 与向量库的增量对账")
    ap.add_argument("--game", default=gamecfg.DEFAULT_GAME,
                    help=f"游戏配置名，默认 {gamecfg.DEFAULT_GAME}")
    ap.add_argument("--chunks", type=Path, default=None)
    args = ap.parse_args()
    cfg = gamecfg.load_game(args.game)
    chunks_path = args.chunks or cfg.chunks_path

    store = retriever.QdrantVectorStore(game=cfg.name)
    qdrant_server.ensure_server()
    existing: dict[str, tuple[int, str]] = {}
    for pid, payload in store.iter_payloads():
        cid = payload.get("chunk_id")
        if cid and cid not in existing:
            existing[cid] = (pid, hashlib.md5(
                payload.get("text", "").encode("utf-8")).hexdigest())
    print(f"[{cfg.label}] 库里向量   : {len(existing)}"
          f"（collection {store.collection}，{chunks_path.name}）")

    n_total = n_same = n_diff = n_new = 0
    new_long: list[tuple[int, str, str]] = []
    legacy_mismatch: list[tuple[str, int, int]] = []
    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            n_total += 1
            cid = c["chunk_id"]
            h = hashlib.md5(c["text"].encode("utf-8")).hexdigest()
            prev = existing.pop(cid, None)
            if prev is None:
                n_new += 1
                new_long.append((len(c["text"]), cid, c["source_title"]))
            elif prev[1] == h:
                n_same += 1
            else:
                n_diff += 1
                legacy_mismatch.append((cid, prev[0], len(c["text"])))
    stale = len(existing)
    print(f"jsonl chunk    : {n_total}")
    print(f"文本一致(跳过) : {n_same}")
    print(f"文本变了(覆盖) : {n_diff}")
    print(f"全新(新向量)   : {n_new}")
    print(f"库有jsonl没有  : {stale}")

    if legacy_mismatch:
        print(f"\n⚠️  有 {len(legacy_mismatch)} 个 chunk_id 文本变了，前 10 个：")
        for cid, pid, ln in legacy_mismatch[:10]:
            print(f"    {cid} (point {pid}, 新长度 {ln})")
        print("    → 这些点会被原地覆盖；若数量接近全量，说明 chunk_id 错位，")
        print("      prose/card 的 id 分配和 v2 不一致，增量会退化成接近全量重算。")

    new_long.sort(reverse=True)
    print(f"\n新 chunk 里最长的 15 个（单位：字）：")
    for ln, cid, title in new_long[:15]:
        print(f"    {ln:>7}  {cid}  {title}")
    n_long = sum(1 for ln, _, _ in new_long if ln >= 1790)
    print(f"    其中 ≥1790 字的 {n_long} 个（审计第 6 节说缺 138 个长 chunk）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
