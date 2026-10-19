#!/usr/bin/env python3
"""阶段 3：本地 embedding → 向量库。

    ./kb/.venv/bin/python embed_index.py                    # 全量重建（先 reset）
    ./kb/.venv/bin/python embed_index.py --append           # 增量：只 embed 新 chunk
    ./kb/.venv/bin/python embed_index.py --append --delete-stale
    ./kb/.venv/bin/python embed_index.py --backend onnx     # 不装 torch 的轻量后端
    ./kb/.venv/bin/python embed_index.py --store numpy      # 无向量库兜底
    ./kb/.venv/bin/python embed_index.py --recreate         # 全量重建

--append 的口径（parse 重跑后补新内容用，不动老向量）：
  * chunk_id 已在库里且文本一致 → 跳过（v2 的 33536 个向量一个都不重算）；
  * chunk_id 在库里但文本变了 → 用原 vector id 原地覆盖；
  * 全新 chunk_id → 接着库里的最大 id 往后排；
  * --delete-stale 再把"库里还有、chunks.jsonl 里没了"的点清掉。

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


def load_sorted(path: Path, limit: int = 0) -> list[dict]:
    """全部读进来按文本长度排序，让每批 padding 浪费最小。

    BERT 按批内最长序列 padding，随机顺序下长尾 chunk（有的上万 token）会把
    整批顶到 512，实测多花 ~1.5x 算力。排序后每批长度接近；CLS 池化不受顺序
    影响，向量按 chunk_id 回填，检索结果与顺序无关。
    """
    chunks = list(iter_chunks(path, limit))
    chunks.sort(key=lambda c: len(c["text"]))
    return chunks


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def existing_index(store: retriever.VectorStore) -> tuple[dict[str, tuple[int, str]], int]:
    """库里已有的 chunk_id → (point_id, text)，外加全库最大 point id。

    qdrant 走 scroll（只要 payload）；numpy 兜底库直接读内存里的快照。
    """
    out: dict[str, tuple[int, str]] = {}
    max_id = -1
    if hasattr(store, "iter_payloads"):
        for pid, payload in store.iter_payloads():
            max_id = max(max_id, pid)
            cid = payload.get("chunk_id")
            if cid and cid not in out:
                out[cid] = (pid, payload.get("text", ""))
    else:
        if hasattr(store, "_load"):
            store._load()
        for pid, payload in zip(getattr(store, "ids", []), getattr(store, "payloads", [])):
            max_id = max(max_id, int(pid))
            cid = payload.get("chunk_id")
            if cid and cid not in out:
                out[cid] = (int(pid), payload.get("text", ""))
    return out, max_id


def main() -> int:
    ap = argparse.ArgumentParser(description="chunk → embedding → 向量库")
    ap.add_argument("--chunks", type=Path, default=CHUNKS_PATH)
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "torch", "onnx"], help="embedding 后端")
    ap.add_argument("--store", default="qdrant", choices=["qdrant", "numpy"])
    ap.add_argument("--recreate", action="store_true", help="先清空再建")
    ap.add_argument("--append", action="store_true",
                    help="增量：跳过库里已有的 chunk，只 embed 新的/文本变了的")
    ap.add_argument("--delete-stale", action="store_true",
                    help="配合 --append：删掉库里已有、chunks.jsonl 里没了的点")
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
    # chunk_id → 复用库里已有的 point id（文本变了的 chunk 原地覆盖，不涨新 id）
    id_override: dict[str, int] = {}
    n_skipped = 0

    def flush() -> int:
        nonlocal buf, next_id
        if not buf:
            return 0
        vecs = encoder.encode_passages([c["text"] for c in buf])
        points = []
        for i, c in enumerate(buf):
            pid = id_override.get(c["chunk_id"])
            if pid is None:
                pid = next_id
                next_id += 1
            payload = {
                "chunk_id": c["chunk_id"], "text": c["text"],
                "source_title": c["source_title"], "source_url": c["source_url"],
                "heading": c.get("heading", ""), "kind": c.get("kind", "prose"),
            }
            if c.get("images"):
                payload["images"] = c["images"]
            points.append({"id": pid, "vector": vecs[i], "payload": payload})
        store.upsert(points)
        n = len(points)
        buf = []
        log.info("已写入 %d 条", n)
        return n

    chunks = load_sorted(args.chunks, args.limit)
    if args.append:
        existing, max_id = existing_index(store)
        n_in_store = store.count()
        pending: list[dict] = []
        for c in chunks:
            prev = existing.pop(c["chunk_id"], None)
            if prev is not None and prev[1] == c["text"]:
                n_skipped += 1          # 库里就有且文本一致，向量一个比特都不动
                continue
            if prev is not None:
                id_override[c["chunk_id"]] = prev[0]   # 文本变了，原地覆盖
            pending.append(c)
        stale = [pid for pid, _ in existing.values()]
        chunks = pending
        next_id = max_id + 1
        print(f"增量对账：库里 {n_in_store} 个向量 → 跳过 {n_skipped}（文本一致）、"
              f"重算 {len(chunks)}（新 chunk / 文本变了，其中 {len(id_override)} 个原地覆盖）")
        if stale:
            if args.delete_stale:
                print(f"清理陈旧向量 {store.delete_ids(stale)} 个"
                      f"（库里有、chunks.jsonl 里没了）")
            else:
                print(f"注意：{len(stale)} 个向量在库里但 chunks.jsonl 里没有了"
                      f"（保留，加 --delete-stale 可清）")

    for chunk in chunks:
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
    if args.append:
        meta["append"] = True
        meta["skipped_unchanged"] = n_skipped
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"索引完成：本次 {total} 条 chunk → 库里共 {n} 个向量，耗时 {elapsed:.0f}s")
    print(f"data/ 占用：{dir_size(DATA_DIR) / 1e6:.1f} MB"
          f"（模型 {dir_size(emb.MODEL_DIR) / 1e6:.0f} MB，向量库 {dir_size(DATA_DIR / 'qdrant') / 1e6:.0f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
