"""本地 cross-encoder 精排：bge-reranker-v2-m3（ONNX int8，CPU，不调外部 API）。

混合检索初排 top-20 之后，用 cross-encoder 对 (query, chunk) 逐对打相关性分，
重排取 top-k。比向量点值更准——它真的把 query 和文档拼在一起过一遍 Transformer。

权重走 kblib.embedder.download_files 下到 data/models/（.gitignore 已排除），
ONNX 变体按 model_int8 → model_quantized → model.onnx 的顺序挑第一个存在的：
int8 在 2 核 CPU 上比 fp32 快 3 倍左右，精排这种粗排任务精度损失可忽略。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Sequence

from .config import DATA_DIR
from .embedder import download_files

log = logging.getLogger("kb.reranker")

REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
# 按顺序挑第一个存在的：int8 在 2 核 CPU 上比 fp32 快 3 倍左右，精排这种
# 粗排后重排的任务精度损失可忽略。只下第一个，剩下的大文件（fp32 2.2G）不碰。
ONNX_CANDIDATES = [
    "onnx/model_int8.onnx",
    "onnx/model_quantized.onnx",
    "onnx/model.onnx",
]
COMMON_FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
]

# bge-reranker-v2-m3 的 max_position_embeddings 是 8192，但 chunk p50 才 ~340 词；
# 512 够用，且 CPU 上速度快一倍
DEFAULT_MAX_LEN = 512


def _wanted_files(local: Path) -> list[str]:
    """已下过 int8 就别再拉其他变体了（fp32 权重 2.2G，纯浪费）。"""
    files = list(COMMON_FILES)
    have = [f for f in ONNX_CANDIDATES if (local / f).exists()]
    if not have:
        files.append(ONNX_CANDIDATES[0])
    return files


def model_dir() -> Path:
    return DATA_DIR / "models" / REPO.replace("/", "__")


def download() -> Path:
    """把 reranker 权重拉到 data/models/（已下过就直接返回）。"""
    return download_files(REPO, _wanted_files(model_dir()))


class CrossEncoderReranker:
    """(query, doc) 对 → 相关性分数，分数越高越相关。"""

    name = "cross-encoder"

    def __init__(self, max_len: int = DEFAULT_MAX_LEN, batch_size: int = 4) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        local = download()
        onnx_path = next((local / f for f in ONNX_CANDIDATES if (local / f).exists()), None)
        if onnx_path is None:
            raise RuntimeError(f"{local} 下没有 ONNX 权重")
        self.max_len = max_len
        self.batch_size = max(1, batch_size)
        self.tok = Tokenizer.from_file(str(local / "tokenizer.json"))
        self.tok.enable_truncation(max_length=max_len)
        self.tok.enable_padding()
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = max(1, os.cpu_count() or 1)
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.sess.get_inputs()}
        log.info("reranker 就绪：%s / %s（max_len=%d）", REPO, onnx_path.name, max_len)

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """pairs: [(query, doc), ...] → 相关性分数（同序）。"""
        if not pairs:
            return []
        import numpy as np

        out: list[float] = []
        for i in range(0, len(pairs), self.batch_size):
            part = list(pairs[i : i + self.batch_size])
            enc = self.tok.encode_batch(part)
            feeds = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
            }
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.array([e.type_ids for e in enc], dtype=np.int64)
            logits = self.sess.run(None, feeds)[0]
            # bge-reranker 单 logit 输出；v2-m3 官方用 sigmoid 压到 (0,1)，排序等价
            out.extend(float(x) for x in np.asarray(logits).reshape(len(part), -1)[:, 0])
        return out


def get_reranker(batch_size: int = 4):
    """取 rerank 器；当前只有本地 cross-encoder 一种实现。"""
    return CrossEncoderReranker(batch_size=batch_size)


if __name__ == "__main__":  # 手动自检：python -m kblib.reranker "举高高是谁"
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    rr = get_reranker()
    q = " ".join(sys.argv[1:]) or "举高高是谁"
    pairs = [(q, "举高高（埃尔德里奇）是白鹰阵营的驱逐舰，画师是梦咲枫。"),
             (q, "12-3 和 12-4 是除了 ex 之外最难的地图，想要压低油耗可以参考本文。")]
    print(dict(zip([p[1][:12] for p in pairs], rr.score(pairs))))
