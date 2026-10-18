#!/usr/bin/env python3
"""阶段 3：本地 embedding → 向量库。

    ./kb/.venv/bin/python embed_index.py                    # 默认 qdrant + torch
    ./kb/.venv/bin/python embed_index.py --backend onnx     # 不装 torch 的轻量后端
    ./kb/.venv/bin/python embed_index.py --store numpy      # 无向量库兜底
    ./kb/.venv/bin/python embed_index.py --recreate         # 全量重建

模型：bge-small-zh（512 维，CPU，不调外部 API）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from kblib import embedder as emb
from kblib import retriever
from kblib.config import CHUNKS_PATH, DATA_DIR

log = logging.getLogger("kb.embed_index")

BATCH = 128
META_PATH = DATA_DIR / "index_meta.json"


def iter_chunks(path: Path, limit: int = 0):
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                return
            line = line.strip()
            if line:
                yield json.loads(line)


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description="chunk → embedding → 向量库")
    ap.add_argument("--chunks", type=Path, default=CHUNKS_PATH)
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "torch", "onnx"], help="embedding 后端")
    ap.add_argument("--store", default="qdrant", choices=["qdrant", "numpy"])
    ap.add_argument("--recreate", action="store_true", help="先清空再建")
    ap.add_argument("--limit", type=int, default=0, help="只索引前 N 条（调试）")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not args.chunks.exists():
        print(f"找不到 {args.chunks}，先跑 parse.py", file=sys.stderr)
        return 1

    t0 = time.time()
    encoder = emb.get_encoder(args.backend)
    print(f"编码器：{encoder.backend} / dim={encoder.dim}")

    store = retriever.get_store(args.store, dim=encoder.dim)
    if args.recreate:
        store.reset()
    print(f"向量库：{store.name}")

    total = 0
    buf: list[dict] = []
    next_id = 0

    def flush() -> int:
        nonlocal buf, next_id
        if not buf:
            return 0
        vecs = encoder.encode_passages([c["text"] for c in buf])
        points = [
            {
                "id": next_id + i,
                "vector": vecs[i].tolist(),
                "payload": {
                    "chunk_id": c["chunk_id"], "text": c["text"],
                    "source_title": c["source_title"], "source_url": c["source_url"],
                    "heading": c.get("heading", ""), "kind": c.get("kind", "prose"),
                },
            }
            for i, c in enumerate(buf)
        ]
        store.upsert(points)
        next_id += len(points)
        n = len(points)
        buf = []
        log.info("已写入 %d 条（累计 %d）", n, next_id)
        return n

    for chunk in iter_chunks(args.chunks, args.limit):
        buf.append(chunk)
        if len(buf) >= args.batch:
            total += flush()
    total += flush()

    n = store.count()
    elapsed = time.time() - t0
    meta = {
        "model": "bge-small-zh",
        "backend": encoder.backend,
        "dim": encoder.dim,
        "store": store.name,
        "chunks_indexed": total,
        "vectors": n,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_sec": round(elapsed, 1),
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"索引完成：{total} 条 chunk → {n} 个向量，耗时 {elapsed:.0f}s")
    print(f"data/ 占用：{dir_size(DATA_DIR) / 1e6:.1f} MB"
          f"（模型 {dir_size(emb.MODEL_DIR) / 1e6:.0f} MB，向量库 {dir_size(DATA_DIR / 'qdrant') / 1e6:.0f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
