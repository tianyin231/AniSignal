#!/usr/bin/env python3
"""把本地 torch 权重导出成 ONNX + int8 动态量化，喂给 kblib.embedder.OnnxEncoder。

背景：本环境 huggingface.co 被墙，Xenova/bge-small-zh-v1.5 的现成 ONNX 权重拉不到；
但 ModelScope 上有 BAAI/bge-small-zh-v1.5 的 torch 权重。BERT 架构导出 ONNX 是标准
操作，导出后用 onnxruntime 动态量化（QInt8），CPU 上比 fp32 torch 快数倍
（实测这台 EPYC（AVX-512 VNNI）约 5x），33674 个 chunk 的全量索引从 6 小时降到 1 小时级。

    ./kb/.venv/bin/python tools/export_onnx.py                 # 默认 BAAI/bge-small-zh-v1.5
    ./kb/.venv/bin/python tools/export_onnx.py --verify        # 额外和 torch 对齐校验

产物落在 data/models/Xenova__bge-small-zh-v1.5/（embedder.OnnxEncoder 默认布局）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 直接跑本文件时能找到 kblib

from kblib import embedder as emb


def main() -> int:
    ap = argparse.ArgumentParser(description="torch 权重 → ONNX int8")
    ap.add_argument("--repo", default="BAAI/bge-small-zh-v1.5", help="torch 权重仓库")
    ap.add_argument("--out", default="Xenova/bge-small-zh-v1.5",
                    help="导出的 ONNX 仓库名（OnnxEncoder 默认读这个）")
    ap.add_argument("--opset", type=int, default=14)
    ap.add_argument("--verify", action="store_true", help="导出后和 torch 编码结果对比")
    args = ap.parse_args()

    import numpy as np
    import torch
    from transformers import AutoModel, AutoTokenizer

    src = emb.download_model(args.repo)
    dst = emb.MODEL_DIR / args.out.replace("/", "__")
    (dst / "onnx").mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(str(src))
    model = AutoModel.from_pretrained(str(src))
    model.eval()

    class _BertWrapper(torch.nn.Module):
        """显式签名，绕开 transformers forward 包装器和位置参数的冲突。"""

        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask, token_type_ids):
            return self.m(input_ids=input_ids, attention_mask=attention_mask,
                          token_type_ids=token_type_ids).last_hidden_state

    dummy = tok(["为这个句子生成表示以用于检索相关文章：碧蓝航线"], return_tensors="pt",
                padding=True, truncation=True, max_length=512)

    onnx_path = dst / "onnx" / "model.onnx"
    print(f"导出 ONNX → {onnx_path}（opset {args.opset}）")
    with torch.no_grad():
        torch.onnx.export(
            _BertWrapper(model),
            (dummy["input_ids"], dummy["attention_mask"], dummy["token_type_ids"]),
            str(onnx_path),
            input_names=["input_ids", "attention_mask", "token_type_ids"],
            output_names=["last_hidden_state"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "seq"},
                "attention_mask": {0: "batch", 1: "seq"},
                "token_type_ids": {0: "batch", 1: "seq"},
                "last_hidden_state": {0: "batch", 1: "seq"},
            },
            opset_version=args.opset,
            do_constant_folding=True,
            dynamo=False,  # 传统 TorchScript 导出，免 onnxscript 依赖
        )
    print(f"fp32 ONNX：{onnx_path.stat().st_size / 1e6:.1f} MB")

    q_path = dst / "onnx" / "model_quantized.onnx"
    from onnxruntime.quantization import QuantType, quantize_dynamic

    print(f"int8 动态量化 → {q_path}")
    quantize_dynamic(str(onnx_path), str(q_path), weight_type=QuantType.QInt8)
    print(f"int8 ONNX：{q_path.stat().st_size / 1e6:.1f} MB")
    onnx_path.unlink()  # 只留量化版，和 Xenova 仓库布局一致

    # tokenizer / 配置文件照搬（同一模型，布局对齐 OnnxEncoder 的下载清单）
    for rel in emb.NEEDED_FILES:
        s, d = src / rel, dst / rel
        if s.exists() and s != d:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
    # OnnxEncoder 靠 config.json 读 dim；BAAI 的 config 和 Xenova 的同构
    (dst / ".complete").touch()
    print(f"布局就绪：{dst}")

    if args.verify:
        texts = ["企业的技能LuckyE可以在空袭后提升航空值",
                 "碧蓝航线企业号航空母舰技能介绍",
                 "今天晚饭吃什么比较好",
                 "Z23的铁血先锋技能可以提升炮击属性"]
        torch_enc = emb.TorchEncoder(args.repo, batch_size=8)
        onnx_enc = emb.OnnxEncoder(args.out, max_len=512)
        vt = torch_enc.encode_passages(texts)
        vo = onnx_enc.encode_passages(texts)
        cos = [float(np.dot(vt[i], vo[i])) for i in range(len(texts))]
        print("torch vs onnx 逐条余弦：", [round(c, 4) for c in cos])
        if min(cos) < 0.99:
            print("对齐不合格，别用 onnx 结果", file=sys.stderr)
            return 1
        print("对齐 OK（≥0.99）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
