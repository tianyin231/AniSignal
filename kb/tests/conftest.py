"""kb/ 自己的测试：把 kb/ 放上 sys.path，让 `from kblib import …` 直接可用。

pytest 默认只把测试文件所在目录（本目录）插进 sys.path，`kblib` 在上一级，
所以在这里补一次。路径按 `__file__` 解析，从哪个 cwd 跑都一样。
"""

from __future__ import annotations

import sys
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parents[1]
if str(KB_ROOT) not in sys.path:
    sys.path.insert(0, str(KB_ROOT))
