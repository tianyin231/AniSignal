#!/usr/bin/env bash
# 全量 fetch 守护循环：一轮到点干净退出，立刻接着跑下一轮，直到抓完。
# 中途被 kill 没关系——分片原子写 + 按磁盘分片自愈续拉，下轮自动接上。
#
#   tools/fetch_loop.sh                # 默认每轮 600s
#   KB_ROUND_SECONDS=300 tools/fetch_loop.sh
#   KB_MAX_ROUNDS=5 tools/fetch_loop.sh
#
set -u
cd "$(dirname "$0")/.."
PY=./.venv/bin/python
ROUND_SECONDS="${KB_ROUND_SECONDS:-600}"
MAX_ROUNDS="${KB_MAX_ROUNDS:-60}"
LOG=data/fetch_loop.log

is_done() {
    $PY - <<'EOF'
import json
from pathlib import Path
p = Path("data/raw/manifest.json")
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
    $PY fetch.py --max-seconds "$ROUND_SECONDS" -v 2>&1 | tee -a data/fetch.log
    if [ "$(is_done)" = "1" ]; then
        echo "===== 全部抓完 $(date '+%m-%d %H:%M:%S')，共 $round 轮" | tee -a "$LOG"
        exit 0
    fi
    sleep 2
done
echo "===== 超过 $MAX_ROUNDS 轮还没抓完，停手等人看" | tee -a "$LOG"
exit 1
