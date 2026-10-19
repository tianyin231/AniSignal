"""BM25 关键词索引：jieba 分词 + Okapi BM25，自己实现，无第三方检索库依赖。

和向量检索互补：向量管语义近似（"治疗"≈"回复"），BM25 管精确字符串
（专有名号"举高高"、装备编号"F6F地狱猫T3"这类）。v1 纯向量在这些词上
翻车过——top5 全是边角料，而 grep 一秒就能找到。

索引落 <游戏 data_dir>/bm25/（.gitignore 已排除，重建约 2 分钟）：

    from kblib.bm25 import BM25Index
    idx = BM25Index.build_from_chunks(game="arknights")   # 扫该游戏的 chunks.jsonl
    idx = BM25Index.load(game="arknights")                # 之后直接用

不给 game 就用默认游戏（azurlane），索引路径与 v2 起的位置一致。

实现要点：
  * 分词结果用词表映射成 int32 倒排表（postings），17M 个词条压到 ~70MB，
    比 list[list[str]] 省一个数量级的内存。
  * 打分用 numpy 按 posting list 累加，单次查询毫秒级。
"""

from __future__ import annotations

import json
import logging
import pickle
import re
import time
from array import array
from pathlib import Path
from typing import Iterable, Sequence

from . import gamecfg
from .gamecfg import GameConfig

log = logging.getLogger("kb.bm25")


def index_paths(cfg: GameConfig | str | None = None, game: str | None = None):
    """(索引文件, meta 文件)。每款游戏一份，互不覆盖。"""
    if isinstance(cfg, str):
        cfg, game = None, cfg
    cfg = cfg or gamecfg.load_game(game)
    d = cfg.bm25_dir
    return d / "index.pkl", d / "meta.json"

K1 = 1.5      # 词频饱和参数，常规值
B = 0.75      # 长度归一化强度，常规值

# 高频虚词：查询里带上它们只会稀释 IDF，索引侧直接丢掉
STOPWORDS = frozenset("""
的 了 是 在 和 与 及 为 有 就 都 也 很 被 把 对 于 上 下 不 到 你 我 他 她 它
们 这 那 之 以 而 并 或 如 若 从 但 却 还 会 能 要 可 等 一个 一些
什么 怎么 怎样 如何 哪些 哪个 多少 是否 有没有 请问 一下 告诉我
""".split())

_CJK_RE = re.compile(r"[一-鿿]")
_ALNUM_RE = re.compile(r"[0-9a-zA-Z]")
_STRIP_RE = re.compile(r"^[^\w一-鿿]+|[^\w一-鿿]+$")


def _clean(tok: str) -> str:
    tok = _STRIP_RE.sub("", tok)
    if not tok:
        return ""
    if len(tok) == 1 and not (_CJK_RE.match(tok) or _ALNUM_RE.match(tok)):
        return ""
    return tok


def tokenize(text: str) -> list[str]:
    """索引和查询共用同一个分词器，保证两边词表一致。"""
    import jieba

    out: list[str] = []
    for w in jieba.lcut((text or "").lower()):
        w = _clean(w)
        if not w or w in STOPWORDS:
            continue
        out.append(w)
    return out


class BM25Index:
    """倒排 BM25 索引。doc 下标 = data/chunks.jsonl 里去重后的行序。"""

    def __init__(self, vocab: dict[str, int], doc_len, postings: dict[int, tuple],
                 doc_ids: Sequence[str], avgdl: float, k1: float = K1, b: float = B) -> None:
        self.vocab = vocab
        self.doc_len = doc_len                # np.int32[n]
        self.postings = postings              # term_id -> (doc ids, freqs)
        self.doc_ids = list(doc_ids)
        self.avgdl = float(avgdl)
        self.k1 = k1
        self.b = b

    # ------------------------------------------------------------ 构建
    @classmethod
    def build_from_chunks(cls, chunks_path: Path | None = None, k1: float = K1,
                          b: float = B, game: str | None = None) -> "BM25Index":
        import numpy as np

        cfg = gamecfg.load_game(game)
        path = chunks_path or cfg.chunks_path
        log.info("开始扫 %s 建 BM25 索引（jieba 分词）", path)
        t0 = time.time()
        vocab: dict[str, int] = {}
        doc_ids: list[str] = []
        doc_len = array("i")
        postings: dict[int, tuple[array, array]] = {}
        seen: set[str] = set()

        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                cid = d["chunk_id"]
                if cid in seen:      # chunks.jsonl 理论上唯一，去重保险
                    continue
                seen.add(cid)
                doc_idx = len(doc_ids)
                doc_ids.append(cid)
                toks = tokenize(d.get("text", ""))
                doc_len.append(len(toks))
                tf: dict[int, int] = {}
                for t in toks:
                    tid = vocab.get(t)
                    if tid is None:
                        tid = len(vocab)
                        vocab[t] = tid
                    tf[tid] = tf.get(tid, 0) + 1
                for tid, cnt in tf.items():
                    p = postings.get(tid)
                    if p is None:
                        postings[tid] = (array("i", [doc_idx]), array("i", [cnt]))
                    else:
                        p[0].append(doc_idx)
                        p[1].append(cnt)

        n = len(doc_ids)
        avgdl = (sum(doc_len) / n) if n else 0.0
        idx = cls(vocab, np.array(doc_len, dtype=np.int32),
                  postings, doc_ids, avgdl, k1, b)
        log.info("BM25 索引建好：%d docs / %d 词条 / 平均 %.1f 词，耗时 %.1fs",
                 n, len(vocab), avgdl, time.time() - t0)
        idx.save(game=game)
        return idx

    # ------------------------------------------------------------ 持久化
    def save(self, path: Path | None = None, game: str | None = None) -> None:
        import numpy as np

        if path is None:
            path, _meta = index_paths(game=game)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump({
                "version": 1,
                "k1": self.k1, "b": self.b,
                "n_docs": len(self.doc_ids), "avgdl": self.avgdl,
                "vocab": self.vocab,
                "doc_len": self.doc_len,
                "doc_ids": self.doc_ids,
                # array('i') 直接 pickle 省一半体积；读回来再转 numpy
                "postings": self.postings,
            }, f, protocol=4)
        tmp.replace(path)
        _p, meta_path = index_paths(game=game)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps({
            "n_docs": len(self.doc_ids), "vocab": len(self.vocab),
            "avgdl": round(self.avgdl, 2), "k1": self.k1, "b": self.b,
            "bytes": path.stat().st_size,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("BM25 索引已存 %s（%.1f MB）", path, path.stat().st_size / 1e6)

    @classmethod
    def load(cls, path: Path | None = None, game: str | None = None) -> "BM25Index":
        import numpy as np

        if path is None:
            path, _meta = index_paths(game=game)
        if not path.exists():
            raise FileNotFoundError(
                f"没有 BM25 索引 {path}，先跑 BM25Index.build_from_chunks(game=...)")
        with open(path, "rb") as f:
            d = pickle.load(f)
        idx = cls(d["vocab"], np.asarray(d["doc_len"], dtype=np.int32), d["postings"],
                  d["doc_ids"], d["avgdl"], d["k1"], d["b"])
        log.info("BM25 索引已加载：%d docs / %d 词条", len(idx.doc_ids), len(idx.vocab))
        return idx

    def __len__(self) -> int:
        return len(self.doc_ids)

    # ------------------------------------------------------------ 检索
    def idf(self, tid: int) -> float:
        import math

        df = len(self.postings[tid][0])
        n = len(self.doc_ids)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """返回 [(chunk_id, bm25_score)]，按分数降序。"""
        import numpy as np

        if not self.doc_ids:
            return []
        toks = tokenize(query)
        if not toks:
            return []
        scores = np.zeros(len(self.doc_ids), dtype=np.float32)
        norm = self.k1 * (1.0 - self.b + self.b * self.doc_len / max(self.avgdl, 1e-9))
        for t in toks:
            tid = self.vocab.get(t)
            if tid is None:
                continue
            docs, freqs = self.postings[tid]
            if not len(docs):
                continue
            docs = np.asarray(docs, dtype=np.int64)
            freqs = np.asarray(freqs, dtype=np.float32)
            contrib = self.idf(tid) * freqs * (self.k1 + 1.0) / (freqs + norm[docs])
            scores += np.bincount(docs, weights=contrib,
                                  minlength=len(scores)).astype(np.float32)
        nz = np.nonzero(scores)[0]
        if not len(nz):
            return []
        nz = nz[np.argsort(-scores[nz], kind="stable")][:top_k]
        return [(self.doc_ids[int(i)], float(scores[int(i)])) for i in nz]


if __name__ == "__main__":
    # parse.py 重跑后重建关键词索引（混合检索的 BM25 那一半）：
    #   ./kb/.venv/bin/python -m kblib.bm25 --game arknights
    import argparse

    ap = argparse.ArgumentParser(description="扫 chunks.jsonl 建 BM25 索引")
    ap.add_argument("--game", default=None,
                    help=f"游戏配置（默认 {gamecfg.DEFAULT_GAME}）")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    t0 = time.time()
    idx = BM25Index.build_from_chunks(game=args.game)
    print(f"BM25 重建完成：{len(idx)} docs → {index_paths(game=args.game)[0]}"
          f"（耗时 {time.time() - t0:.0f}s）")
