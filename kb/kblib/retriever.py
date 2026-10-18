"""检索接口抽象层：换向量库只改这一个文件。

  * QdrantVectorStore —— 本地 Qdrant 单二进制（默认）
  * NumpyVectorStore  —— 无依赖兜底：全量驻留内存暴力余弦（数据量 <10 万向量时够用）

两条路都实现同一组方法：upsert / search / count / reset。
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
from .config import DATA_DIR

log = logging.getLogger("kb.retriever")

COLLECTION = "blhx_kb"


@dataclass
class Hit:
    chunk_id: str
    score: float
    text: str
    source_title: str
    source_url: str
    heading: str = ""
    kind: str = "prose"

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
