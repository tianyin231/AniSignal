#!/usr/bin/env python3
"""把原神知识库的四个产物推到 tianyin231/genshin-kb（只覆盖本轮改动的文件）。

凭证走 custom.huggingface connector 的 surrogate：authd 只给 `hsurr:` 占位值，
Sentinel 在放行对 huggingface.co 的请求时才换成真 token，所以脚本里、环境变量里、
日志里都不会出现真 key。

    HF_HUB_DISABLE_XET=1 python hf_upload/upload_genshin_fix.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
import dynamic_credentials as dc  # noqa: E402

REPO = "tianyin231/genshin-kb"
GAME = "genshin"
DATA = KB_DIR / "data" / GAME

# 本轮改了文本的四个产物；raw/ 和 qdrant/ 不动（raw 没变，qdrant 是全游戏共用的
# 一份存储，不是原神独有，传上去只会传错）
ITEMS = ["chunks.jsonl", "kb.db", "bm25", "eval_report.json"]


def main() -> int:
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    entry = dc.dynamic_credential_entry("custom.huggingface")
    token = entry["surrogate"]

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    print("身份：", api.whoami().get("name"))

    for name in ITEMS:
        src = DATA / name
        if not src.exists():
            print("跳过（不存在）：", name)
            continue
        dest = f"{GAME}/{name}"
        if src.is_dir():
            api.upload_folder(folder_path=str(src), path_in_repo=dest,
                              repo_id=REPO, repo_type="dataset", commit_message=f"update {dest}")
        else:
            api.upload_file(path_or_fileobj=str(src), path_in_repo=dest,
                            repo_id=REPO, repo_type="dataset", commit_message=f"update {dest}")
        print("已上传：", dest, f"（{src.stat().st_size if src.is_file() else '目录'}）")
    print("完成：", f"https://huggingface.co/datasets/{REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
