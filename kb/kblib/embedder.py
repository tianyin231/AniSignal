"""本地 embedding 编码器：bge-small-zh，CPU 跑，不调任何外部 API。

两个后端，接口一致：
  * torch —— transformers + BAAI/bge-small-zh-v1.5（brief 首选）
  * onnx  —— onnxruntime + Xenova/bge-small-zh-v1.5 的 ONNX 权重（免 torch 依赖）

torch 后端不依赖 sentence-transformers：bge 系就是 BertModel + CLS 池化 + L2
归一化，池化开关直接读模型目录里的 1_Pooling/config.json，行为与
sentence-transformers 加载同一模型时一致（本环境装 sklearn/scipy 太贵，砍掉）。

模型文件缓存在 data/models/，HF 直连失败自动换 hf-mirror。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("kb.embedder")

MODEL_DIR = DATA_DIR / "models"
HF_ENDPOINTS = [
    os.environ.get("HF_ENDPOINT", "https://huggingface.co"),
    "https://hf-mirror.com",
]

# bge 中文模型的检索指令（官方推荐：query 加指令，passage 不加）
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

NEEDED_FILES = [
    "config.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "tokenizer.json",
    "sentence_bert_config.json",
    "modules.json",
    "1_Pooling/config.json",
]
ONNX_FILES = ["onnx/model_quantized.onnx", "onnx/model.onnx", "onnx/model_fp16.onnx"]


def _curl_download(url: str, dest: Path, timeout: int = 300) -> None:
    """用 curl 下载——本环境网络沙箱只放行 curl 出网，Python 原生 socket 会被丢包。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    proc = subprocess.run(
        [shutil.which("curl") or "curl", "-sSL", "--fail", "-m", str(timeout),
         "-A", "anisignal-kb/0.1", "-o", str(tmp), "--", url],
        capture_output=True, timeout=timeout + 30,
    )
    if proc.returncode != 0 or not tmp.exists():
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"curl {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    tmp.replace(dest)


def download_model(repo: str, extra: list[str] | None = None) -> Path:
    """把 HF 仓库拉到 data/models/<repo 名>/，返回本地目录。"""
    local = MODEL_DIR / repo.replace("/", "__")
    if (local / ".complete").exists():
        return local
    local.mkdir(parents=True, exist_ok=True)
    files = list(NEEDED_FILES) + list(extra or [])
    for rel in files:
        dest = local / rel
        if dest.exists() and dest.stat().st_size > 0:
            continue
        last_err: Exception | None = None
        for ep in HF_ENDPOINTS:
            url = f"{ep}/{repo}/resolve/main/{rel}"
            try:
                _curl_download(url, dest)
                log.info("下载 %s → %s（%.1f MB）", rel, dest, dest.stat().st_size / 1e6)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001 - 换镜像重试
                last_err = e
                if dest.exists():
                    dest.unlink()
        if last_err is not None:
            raise RuntimeError(f"下载失败 {rel}: {last_err}")
    (local / ".complete").touch()
    return local


class BaseEncoder:
    dim = 512
    backend = "?"

    def encode(self, texts: list[str], is_query: bool = False):
        raise NotImplementedError

    def encode_queries(self, texts: list[str]):
        return self.encode(list(texts), is_query=True)

    def encode_passages(self, texts: list[str]):
        return self.encode(list(texts), is_query=False)


class TorchEncoder(BaseEncoder):
    """transformers 后端（不依赖 sentence-transformers）。"""

    backend = "torch"

    def __init__(self, repo: str = "BAAI/bge-small-zh-v1.5", batch_size: int = 32,
                 max_len: int = 512) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.batch_size = batch_size
        self.max_len = max_len
        local = download_model(repo)          # 先把权重拉到本地再加载
        self.tok = AutoTokenizer.from_pretrained(str(local))
        self.model = AutoModel.from_pretrained(str(local))
        self.model.eval()
        self.dim = int(self.model.config.hidden_size)
        # 池化方式跟模型目录里的配置走（bge 系是 CLS）
        pool_cfg = local / "1_Pooling" / "config.json"
        self.cls_pool = True
        if pool_cfg.exists():
            self.cls_pool = bool(
                json.loads(pool_cfg.read_text(encoding="utf-8")).get("pooling_mode_cls_token", True)
            )
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        log.info("torch 编码器就绪：%s（dim=%d，%s 池化）",
                 repo, self.dim, "CLS" if self.cls_pool else "mean")

    def encode(self, texts: list[str], is_query: bool = False):
        import numpy as np

        torch = self.torch
        batch = [QUERY_INSTRUCTION + t for t in texts] if is_query else list(texts)
        outs = []
        with torch.no_grad():
            for i in range(0, len(batch), self.batch_size):
                enc = self.tok(batch[i : i + self.batch_size], padding=True,
                               truncation=True, max_length=self.max_len,
                               return_tensors="pt")
                h = self.model(**enc).last_hidden_state
                if self.cls_pool:
                    emb = h[:, 0]
                else:
                    mask = enc["attention_mask"].unsqueeze(-1).to(h.dtype)
                    emb = (h * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                emb = torch.nn.functional.normalize(emb, p=2, dim=1)
                outs.append(emb.cpu().numpy().astype(np.float32))
        if not outs:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack(outs)


class OnnxEncoder(BaseEncoder):
    """onnxruntime + tokenizers 后端（不装 torch）。"""

    backend = "onnx"

    def __init__(self, repo: str = "Xenova/bge-small-zh-v1.5", max_len: int = 512,
                 batch_size: int = 64) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        self.batch_size = max(1, batch_size)
        local = download_model(repo, extra=ONNX_FILES)
        self.repo = repo
        self.tok = Tokenizer.from_file(str(local / "tokenizer.json"))
        self.tok.enable_truncation(max_length=max_len)
        self.tok.enable_padding()
        onnx_path = next((local / f for f in ONNX_FILES if (local / f).exists()), None)
        if onnx_path is None:
            raise RuntimeError(f"{local} 下没有 ONNX 权重")
        # 池化方式跟模型目录里的配置走（bge 系是 CLS，跟 torch 后端保持一致）
        self.cls_pool = True
        pool_cfg = local / "1_Pooling" / "config.json"
        if pool_cfg.exists():
            self.cls_pool = bool(
                json.loads(pool_cfg.read_text(encoding="utf-8")).get("pooling_mode_cls_token", True)
            )
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = max(1, os.cpu_count() or 1)
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.sess.get_inputs()}
        cfg = local / "config.json"
        self.dim = 512
        if cfg.exists():
            self.dim = int(json.loads(cfg.read_text()).get("hidden_size", 512))
        log.info("onnx 编码器就绪：%s / %s（dim=%d）", repo, onnx_path.name, self.dim)

    def encode(self, texts: list[str], is_query: bool = False):
        batch = [QUERY_INSTRUCTION + t for t in texts] if is_query else list(texts)
        np = self.np
        outs = []
        # 分批跑：onnxruntime 一次吃太多文本，中间激活能顶到 GB 级（无 swap 会 OOM）
        for i in range(0, len(batch), self.batch_size):
            part = batch[i : i + self.batch_size]
            enc = self.tok.encode_batch(part)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.array([e.type_ids for e in enc], dtype=np.int64)
            out = self.sess.run(None, feeds)[0]
            if out.ndim == 2:  # 有些导出直接给 pooler 输出
                emb = out
            elif self.cls_pool:
                emb = out[:, 0]
            else:
                m = mask[:, :, None].astype(np.float32)
                emb = (out * m).sum(axis=1) / np.clip(m.sum(axis=1), 1.0, None)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            outs.append(emb / np.clip(norms, 1e-9, None))
        if not outs:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack(outs).astype(np.float32)


def get_encoder(backend: str = "torch") -> BaseEncoder:
    backend = (backend or "auto").lower()
    if backend in ("torch", "auto"):
        try:
            return TorchEncoder()
        except Exception as e:
            if backend == "torch":
                raise
            log.warning("torch 后端不可用（%s），改用 onnx", e)
    return OnnxEncoder()
