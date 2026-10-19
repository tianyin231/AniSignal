#!/usr/bin/env bash
# 全量 fetch 守护循环：一轮到点干净退出，立刻接着跑下一轮，直到抓完。
# 中途被 kill 没关系——分片原子写 + 按磁盘分片自愈续拉，下轮自动接上。
#
#   tools/fetch_loop.sh                    # 默认游戏（azurlane），每轮 600s
#   tools/fetch_loop.sh --game arknights   # 换游戏
#   KB_ROUND_SECONDS=300 tools/fetch_loop.sh
#   KB_MAX_ROUNDS=5 tools/fetch_loop.sh
#
set -u
cd "$(dirname "$0")/.."
PY=./.venv/bin/python
GAME="${KB_GAME:-azurlane}"
# 第一个非选项参数当游戏名：fetch_loop.sh arknights
for a in "$@"; do case "$a" in --*) ;; *) GAME="$a";; esac; done
ROUND_SECONDS="${KB_ROUND_SECONDS:-600}"
MAX_ROUNDS="${KB_MAX_ROUNDS:-60}"
DATA_DIR="$($PY -c "import sys; sys.path.insert(0,'.'); from kblib import gamecfg; print(gamecfg.load_game('$GAME').data_dir)")"
LOG="$DATA_DIR/fetch_loop.log"

is_done() {
    $PY - "$1" <<'EOF'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    print(0)
else:
    print(1 if json.loads(p.read_text(encoding="utf-8")).get("finished_at") else 0)
EOF
}

round=0
while [ "$round" -lt "$MAX_ROUNDS" ]; do
    round=$((round + 1))
    echo "===== round $round $(date '+%m-%d %H:%M:%S')（每轮 ${ROUND_SECONDS}s）" | tee -a "$LOG"
    $PY fetch.py --game "$GAME" --max-seconds "$ROUND_SECONDS" -v 2>&1 | tee -a "$DATA_DIR/fetch.log"
    if [ "$(is_done)" = "1" ]; then
        echo "===== 全部抓完 $(date '+%m-%d %H:%M:%S')，共 $round 轮" | tee -a "$LOG"
        exit 0
    fi
    sleep 2
done
echo "===== 超过 $MAX_ROUNDS 轮还没抓完，停手等人看" | tee -a "$LOG"
exit 1
