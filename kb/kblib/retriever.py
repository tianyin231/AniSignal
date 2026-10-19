"""检索接口抽象层：换向量库只改这一个文件。

  * QdrantVectorStore —— 本地 Qdrant 单二进制（默认）
  * NumpyVectorStore  —— 无依赖兜底：全量驻留内存暴力余弦（数据量 <10 万向量时够用）
  * HybridStore       —— 向量 + BM25 混合（RRF 融合初排 → cross-encoder rerank 精排）

前两条路实现同一组方法：upsert / search / count / reset。
HybridStore 包一条 VectorStore + 一个 BM25Index，search 签名向后兼容
（多一个可选 query 参数，不给就退化成纯向量检索）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import qdrant_server
from .bm25 import BM25Index
from .config import CHUNKS_PATH, DATA_DIR

log = logging.getLogger("kb.retriever")

COLLECTION = "blhx_kb"

# RRF 融合常数。60 是常规值（Cormack et al. 2009 的推荐量级，ES/Lucene 混合查询同量级）：
# 它决定"排第 1 的压倒性优势"和"多路共识"之间的平衡，对具体取值不敏感
RRF_K = 60


@dataclass
class Hit:
    chunk_id: str
    score: float
    text: str
    source_title: str
    source_url: str
    heading: str = ""
    kind: str = "prose"
    # 混合检索的中间分数，排查"为什么是这条"用；纯向量检索时为 0
    rrf: float = 0.0
    bm25: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


class VectorStore(ABC):
    """向量库统一接口。"""

    name = "abstract"

    @abstractmethod
    def upsert(self, points: Sequence[dict]) -> None:
        """points: [{id, vector(list[float]), payload(dict)}]"""

    @abstractmethod
    def search(self, vector: Sequence[float], top_k: int = 5,
               kind: str | None = None) -> list[Hit]:
        ...

    @abstractmethod
    def count(self) -> int:
        ...

    @abstractmethod
    def reset(self) -> None:
        ...


# ---------------------------------------------------------------- Qdrant
class QdrantVectorStore(VectorStore):
    name = "qdrant"

    def __init__(self, collection: str = COLLECTION, dim: int = 512, url: str | None = None) -> None:
        self.collection = collection
        self.dim = dim
        self.url = (url or qdrant_server.BASE_URL).rstrip("/")

    # -- 底层 REST --
    def _req(self, method: str, path: str, body: Any = None, timeout: int = 120) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.url + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r) if r.length != 0 else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"Qdrant {method} {path} → HTTP {e.code}: {detail}") from e

    def ensure_collection(self, recreate: bool = False) -> None:
        qdrant_server.ensure_server()
        if recreate:
            self._req("DELETE", f"/collections/{self.collection}")
        try:
            self._req("GET", f"/collections/{self.collection}")
            return
        except RuntimeError:
            pass
        self._req("PUT", f"/collections/{self.collection}", {
            "vectors": {"size": self.dim, "distance": "Cosine", "on_disk": True},
            "optimizers_config": {"default_segment_number": 2},
            "hnsw_config": {"m": 16, "ef_construct": 128},
        })
        log.info("已建 collection %s（dim=%d, cosine）", self.collection, self.dim)

    # -- 接口 --
    def upsert(self, points: Sequence[dict]) -> None:
        self.ensure_collection()
        for i in range(0, len(points), 256):
            batch = points[i : i + 256]
            self._req("PUT", f"/collections/{self.collection}/points?wait=true", {
                "points": [
                    {"id": p["id"], "vector": list(p["vector"]), "payload": p["payload"]}
                    for p in batch
                ]
            })

    def search(self, vector: Sequence[float], top_k: int = 5,
               kind: str | None = None) -> list[Hit]:
        qdrant_server.ensure_server()
        body: dict[str, Any] = {
            "vector": list(vector), "limit": top_k, "with_payload": True,
        }
        if kind:
            body["filter"] = {"must": [{"key": "kind", "match": {"value": kind}}]}
        res = self._req("POST", f"/collections/{self.collection}/points/search", body)
        out = []
        for r in res.get("result", []):
            p = r.get("payload") or {}
            out.append(Hit(
                chunk_id=p.get("chunk_id", ""), score=float(r.get("score", 0.0)),
                text=p.get("text", ""), source_title=p.get("source_title", ""),
                source_url=p.get("source_url", ""), heading=p.get("heading", ""),
                kind=p.get("kind", "prose"),
            ))
        return out

    def count(self) -> int:
        qdrant_server.ensure_server()
        res = self._req("POST", f"/collections/{self.collection}/points/count", {"exact": True})
        return int(res.get("result", {}).get("count", 0))

    def iter_payloads(self, page: int = 1000) -> Iterable[tuple[int, dict]]:
        """滚动全库，yield (point_id, payload)。只要 payload 不要向量，做增量对账用。"""
        qdrant_server.ensure_server()
        offset: Any = None
        while True:
            body: dict[str, Any] = {"limit": page, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            res = self._req("POST", f"/collections/{self.collection}/points/scroll", body)
            for p in res.get("result", {}).get("points", []):
                yield int(p["id"]), (p.get("payload") or {})
            offset = res.get("result", {}).get("next_page_offset")
            if offset is None:
                return

    def max_point_id(self) -> int:
        ids = [pid for pid, _ in self.iter_payloads()]
        return max(ids) if ids else -1

    def delete_ids(self, ids: Sequence[int]) -> int:
        if not ids:
            return 0
        qdrant_server.ensure_server()
        n = 0
        for i in range(0, len(ids), 500):
            batch = [int(x) for x in ids[i : i + 500]]
            self._req("POST", f"/collections/{self.collection}/points/delete",
                      {"points": batch})
            n += len(batch)
        return n

    def set_payload_where(self, key: str, value: str, payload: dict) -> None:
        """按 payload 字段给命中的点打补丁（不动向量）。"""
        qdrant_server.ensure_server()
        self._req("POST", f"/collections/{self.collection}/points/payload", {
            "payload": payload,
            "filter": {"must": [{"key": key, "match": {"value": value}}]},
            "wait": True,
        })

    def reset(self) -> None:
        qdrant_server.ensure_server()
        self._req("DELETE", f"/collections/{self.collection}")
        log.info("已删除 collection %s", self.collection)


# ---------------------------------------------------------------- 兜底
class NumpyVectorStore(VectorStore):
    """无第三方向量库时的兜底实现：numpy 暴力检索，快照落盘。"""

    name = "numpy"

    def __init__(self, path: Path | None = None) -> None:
        import numpy as np

        self.np = np
        self.path = path or (DATA_DIR / "vectors.npz")
        self.ids: list[int] = []
        self.payloads: list[dict] = []
        self.mat = np.zeros((0, 0), dtype=np.float32)

    def _load(self) -> None:
        if self.mat.size or not self.path.exists():
            return
        z = self.np.load(self.path, allow_pickle=False)
        self.mat = z["vectors"]
        self.ids = [int(i) for i in z["ids"]]
        self.payloads = json.loads(str(z["payloads"]))

    def upsert(self, points: Sequence[dict]) -> None:
        import numpy as np

        self._load()
        if not points:
            return
        vecs = np.array([p["vector"] for p in points], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms
        if self.mat.size:
            self.mat = np.vstack([self.mat, vecs])
        else:
            self.mat = vecs
        self.ids.extend(int(p["id"]) for p in points)
        self.payloads.extend(p["payload"] for p in points)
        np.savez(self.path, vectors=self.mat, ids=np.array(self.ids),
                 payloads=json.dumps(self.payloads, ensure_ascii=False))

    def search(self, vector: Sequence[float], top_k: int = 5,
               kind: str | None = None) -> list[Hit]:
        import numpy as np

        self._load()
        if not self.mat.size:
            return []
        q = np.array(vector, dtype=np.float32)
        q = q / max(float(np.linalg.norm(q)), 1e-9)
        scores = self.mat @ q
        order = np.argsort(-scores)[: top_k * 4 if kind else top_k]
        out: list[Hit] = []
        for idx in order:
            p = self.payloads[int(idx)]
            if kind and p.get("kind") != kind:
                continue
            out.append(Hit(
                chunk_id=p.get("chunk_id", ""), score=float(scores[int(idx)]),
                text=p.get("text", ""), source_title=p.get("source_title", ""),
                source_url=p.get("source_url", ""), heading=p.get("heading", ""),
                kind=p.get("kind", "prose"),
            ))
            if len(out) >= top_k:
                break
        return out

    def count(self) -> int:
        self._load()
        return len(self.ids)

    def reset(self) -> None:
        self.mat = self.np.zeros((0, 0), dtype=self.np.float32)
        self.ids, self.payloads = [], []
        if self.path.exists():
            self.path.unlink()


def get_store(name: str | None = None, dim: int = 512) -> VectorStore:
    """按名字取向量库；默认 qdrant，qdrant 不可用自动退 numpy。"""
    name = (name or "qdrant").lower()
    if name == "numpy":
        return NumpyVectorStore()
    if name == "qdrant":
        try:
            qdrant_server.ensure_server()
            return QdrantVectorStore(dim=dim)
        except Exception as e:  # pragma: no cover - 环境受限时兜底
            log.warning("Qdrant 不可用（%s），退回 numpy 兜底库", e)
            return NumpyVectorStore()
    raise ValueError(f"未知向量库：{name}")


# ---------------------------------------------------------------- 混合检索
class HybridStore(VectorStore):
    """向量 + BM25 混合检索。

    链路：向量召回 recall_k 条 + BM25 召回 recall_k 条 → RRF 融合取前 recall_k 条
    → cross-encoder 精排取前 top_k 条。

    为什么不是一路通吃：
      * 纯向量在专有名词上翻过车（"举高高是谁"top5 全边角料，关键词 grep 一秒命中）；
      * 纯 BM25 在语义改写上瞎（"治疗机制"问不倒同义词）。
    RRF 只用排名不用原始分，正好避开两路分数量纲不可比的问题。
    """

    name = "hybrid"

    def __init__(self, vector: VectorStore | None = None, bm25: BM25Index | None = None,
                 reranker=None, rrf_k: int = RRF_K, recall_k: int = 20,
                 rerank: bool = True) -> None:
        self.vector = vector
        self.rrf_k = rrf_k
        self.recall_k = recall_k
        self.bm25 = bm25 if bm25 is not None else self._load_bm25()
        self.reranker = None
        if rerank:
            self.reranker = reranker if reranker is not None else self._load_reranker()
        self._payloads: dict[str, dict] | None = None

    # -- 依赖装配：缺什么补什么，补不上就降级 --
    @staticmethod
    def _load_bm25() -> BM25Index:
        try:
            return BM25Index.load()
        except FileNotFoundError:
            log.warning("没有 BM25 索引，现场建一个（约 1 分钟）")
            return BM25Index.build_from_chunks()

    @staticmethod
    def _load_reranker():
        try:
            from . import reranker as rr

            return rr.get_reranker()
        except Exception as e:  # noqa: BLE001 - 模型缺失就退回纯 RRF
            log.warning("reranker 不可用（%s），混合检索只做 RRF 融合", e)
            return None

    # -- payload：BM25 命中的 chunk 可能不在向量库里，文本从 chunks.jsonl 补 --
    @property
    def payloads(self) -> dict[str, dict]:
        if self._payloads is None:
            self._payloads = {}
            with open(CHUNKS_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    self._payloads[d["chunk_id"]] = d
            log.info("已加载 %d 条 chunk payload", len(self._payloads))
        return self._payloads

    def _hit_from_payload(self, cid: str, score: float = 0.0, rrf: float = 0.0,
                          bm25: float = 0.0) -> Hit:
        p = self.payloads.get(cid, {})
        return Hit(chunk_id=cid, score=score, text=p.get("text", ""),
                   source_title=p.get("source_title", ""),
                   source_url=p.get("source_url", ""), heading=p.get("heading", ""),
                   kind=p.get("kind", "prose"), rrf=rrf, bm25=bm25)

    # -- 接口（upsert/count/reset 委托给向量库，混合检索本身只读）--
    def upsert(self, points: Sequence[dict]) -> None:
        if self.vector is None:
            raise RuntimeError("HybridStore 没有挂向量库")
        self.vector.upsert(points)

    def count(self) -> int:
        return len(self.bm25) if self.vector is None else self.vector.count()

    def reset(self) -> None:
        if self.vector is not None:
            self.vector.reset()

    def search(self, vector: Sequence[float] | None = None, top_k: int = 5,
               kind: str | None = None, query: str | None = None) -> list[Hit]:
        """混合检索。

        vector + query 都传 → 完整链路；只传 vector → 纯向量（v1 行为）；
        只传 query → 纯 BM25。top_k 是最终返回条数，和 v1 同口径。
        """
        if not query:
            if vector is None:
                return []
            return self.vector.search(vector, top_k=top_k, kind=kind)  # type: ignore[union-attr]

        vec_hits: list[Hit] = []
        if vector is not None and self.vector is not None:
            vec_hits = self.vector.search(vector, top_k=self.recall_k, kind=kind)
        bm25_raw = self.bm25.search(query, top_k=self.recall_k)

        # -- RRF：rank 从 1 开始，分数 = Σ 1/(k + rank) --
        fused: dict[str, float] = {}
        bm25_score: dict[str, float] = {}
        for rank, h in enumerate(vec_hits, start=1):
            fused[h.chunk_id] = fused.get(h.chunk_id, 0.0) + 1.0 / (self.rrf_k + rank)
        for rank, (cid, s) in enumerate(bm25_raw, start=1):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (self.rrf_k + rank)
            bm25_score[cid] = s

        order = sorted(fused.items(), key=lambda kv: -kv[1])[: self.recall_k]
        if not order:
            return []

        # -- cross-encoder 精排；模型没就绪就按 RRF 顺序截断 --
        if self.reranker is not None:
            pairs = [(query, self.payloads[cid].get("text", "")) for cid, _ in order]
            scores = self.reranker.score(pairs)
            ranked = sorted(zip([c for c, _ in order], scores), key=lambda kv: -kv[1])
            final = [(cid, float(s), fused[cid]) for cid, s in ranked[:top_k]]
        else:
            final = [(cid, fused[cid], fused[cid]) for cid, _ in order[:top_k]]

        out = []
        for cid, score, rrf in final:
            out.append(self._hit_from_payload(cid, score=score, rrf=rrf,
                                              bm25=bm25_score.get(cid, 0.0)))
        return out


def get_hybrid_store(vector_name: str | None = None, dim: int = 512,
                     rerank: bool = True, recall_k: int = 20) -> HybridStore:
    """装一条混合检索链路：向量库 + BM25 + reranker。"""
    vec = None
    try:
        vec = get_store(vector_name, dim=dim)
        if vec.count() == 0:
            log.warning("向量库是空的，混合检索只走 BM25")
            vec = None
    except Exception as e:  # noqa: BLE001 - 向量库不可用就退回纯 BM25
        log.warning("向量库不可用（%s），混合检索只走 BM25", e)
    return HybridStore(vector=vec, rerank=rerank, recall_k=recall_k)
