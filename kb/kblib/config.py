"""全局路径与常量（与具体游戏无关的那部分）。

游戏相关的旋钮（wiki 地址、infobox 白名单、chunk 规则、collection 名…）全部
搬到 kb/games/*.yaml，由 kblib.gamecfg 读取；这里只留跨游戏共享的东西：

  * KB_ROOT / DATA_DIR —— kb/ 与 kb/data/（DATA_DIR 可用 KB_DATA_DIR 覆盖）
  * GAMES_DIR          —— kb/games/，一款游戏一份 YAML
  * 模型权重目录、Qdrant 二进制/存储目录 —— 游戏无关，所有游戏共用一份

旧版本文件里那堆 INFOBOX_TEMPLATES / NAV_TEMPLATE_PREFIXES / WIKI_BASE 常量
已原样搬进 games/azurlane.yaml，见 kblib.gamecfg 的说明。
"""

from __future__ import annotations

import os
from pathlib import Path

# kb/ 目录（本文件位于 kb/kblib/config.py）
KB_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = Path(os.environ.get("KB_DATA_DIR", KB_ROOT / "data"))
GAMES_DIR = KB_ROOT / "games"

# 模型权重（embedding / reranker 共用，游戏无关）缓存在 data/models/
MODEL_DIR = DATA_DIR / "models"

# Qdrant 单二进制与其存储目录：一个进程服务所有游戏，collection 按游戏分开
QDRANT_BIN = KB_ROOT / "vendor" / "qdrant"
QDRANT_STORAGE_DIR = DATA_DIR / "qdrant"
QDRANT_PID_FILE = DATA_DIR / "qdrant.pid"
QDRANT_PORT = int(os.environ.get("KB_QDRANT_PORT", "6333"))
