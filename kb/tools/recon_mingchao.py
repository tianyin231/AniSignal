#!/usr/bin/env python3
"""chunk/向量对账：chunks.jsonl vs qdrant mingchao_kb。

三件事：
  1. 两边的 chunk_id 集合是否一一对应（多/少/文本不一致都列出来）；
  2. 向量维度/数量是否对得上；
  3. payload 关键字段（source_title/heading/kind）有没有缺。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kblib import gamecfg, qdrant_server, retriever  # noqa: E402


def main() -> int:
    cfg = gamecfg.load_game("mingchao")
    store = retriever.QdrantVectorStore(game=cfg.name)
    qdrant_server.ensure_server()

    have: dict[str, tuple[int, str]] = {}
    n_vec = 0
    for pid, payload in store.iter_payloads():
        n_vec += 1
        cid = payload.get("chunk_id")
        if cid:
            have[cid] = (pid, hashlib.md5(payload.get("text", "").encode()).hexdigest())

    n_total = n_same = n_diff = n_new = 0
    bad_payload = []
    with open(cfg.chunks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            n_total += 1
            h = hashlib.md5(c["text"].encode()).hexdigest()
            prev = have.pop(c["chunk_id"], None)
            if prev is None:
                n_new += 1
            elif prev[1] == h:
                n_same += 1
            else:
                n_diff += 1

    print(f"chunks.jsonl      : {n_total}")
    print(f"qdrant 向量       : {n_vec}（collection {store.collection}）")
    print(f"  文本一致        : {n_same}")
    print(f"  文本不一致      : {n_diff}")
    print(f"  jsonl 有库里没有: {n_new}")
    print(f"  库有 jsonl 没有 : {len(have)}")
    if have:
        for cid, (pid, _) in list(have.items())[:10]:
            print(f"    多余: {cid} (point {pid})")

    # payload 完整性
    n_chk = 0
    for pid, payload in store.iter_payloads():
        n_chk += 1
        for k in ("chunk_id", "text", "source_title", "kind"):
            if not payload.get(k):
                bad_payload.append((pid, k))
                break
    print(f"payload 缺字段    : {len(bad_payload)}")

    ok = (n_total == n_vec == n_same) and n_diff == 0 and n_new == 0 and not have
    print("\n对账结果：" + ("✅ 完全一致" if ok else "❌ 有差"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
