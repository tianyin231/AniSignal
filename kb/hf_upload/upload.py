#!/usr/bin/env python3
"""把炼好的知识库数据推到 HuggingFace Dataset 仓库。

用法：
    export HF_TOKEN=hf_xxx          # https://huggingface.co/settings/tokens 建 write token
    export HF_REPO=tianyin231/azurlane-kb   # 目标仓库（不存在会自动建，private）
    python hf_upload/upload.py

说明：
    - data/ 下的产物按游戏分目录上传；模型权重不传（公开可下载）。
    - 大文件自动走 LFS（见 .gitattributes.hf）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = KB_DIR / "data"

# 每个游戏一个子目录；现在只有碧蓝航线
GAME = os.environ.get("KB_GAME", "azurlane")
REPO = os.environ.get("HF_REPO", "")
TOKEN = os.environ.get("HF_TOKEN", "")

# 传这些，logs / pid / 临时文件不传
INCLUDE = [
    "raw",               # wikitext 分片（含 manifest）
    "chunks.jsonl",      # 清洗后 chunk
    "kb.db",             # 结构化表
    "bm25",              # BM25 索引
    "qdrant",            # 向量库（可直接用，省重建）
    "eval_report_v2.json",
    "eval_report.json",
    "index_meta.json",
]


def main() -> int:
    if not REPO or not TOKEN:
        print("需要 HF_REPO 和 HF_TOKEN 环境变量", file=sys.stderr)
        return 1
    try:
        from huggingface_hub import HfApi, create_repo
    except ImportError:
        print("先装：pip install huggingface_hub", file=sys.stderr)
        return 1

    api = HfApi(token=TOKEN)
    create_repo(REPO, repo_type="dataset", private=True, exist_ok=True,
                token=TOKEN)
    print("仓库就绪：", REPO)

    # dataset 卡 + LFS 配置先推
    api.upload_file(
        path_or_fileobj=str(KB_DIR / "hf_upload" / "README.dataset.md"),
        path_in_repo=f"{GAME}/README.md",
        repo_id=REPO, repo_type="dataset", token=TOKEN,
    )
    api.upload_file(
        path_or_fileobj=str(KB_DIR / "hf_upload" / ".gitattributes.hf"),
        path_in_repo=".gitattributes",
        repo_id=REPO, repo_type="dataset", token=TOKEN,
    )

    for name in INCLUDE:
        src = DATA_DIR / name
        if not src.exists():
            print("跳过（不存在）：", name)
            continue
        print("上传：", name)
        api.upload_folder(
            folder_path=str(src),
            path_in_repo=f"{GAME}/{name}",
            repo_id=REPO, repo_type="dataset", token=TOKEN,
        )
    print("完成。拉取：huggingface_hub.snapshot_download(%r, repo_type='dataset')" % REPO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
