#!/bin/bash
# embedding 断点续跑：embed_index.py --append 的长任务启动壳。
#
#   tools/embed_resume.sh arknights              # 方舟：接着库里已有的向量往下跑
#   tools/embed_resume.sh arknights --enc-batch 4 # 内存吃紧再压一档
#
# 两件事是裸跑 python 没有的：
#  1. oom_score_adj=1000 —— 2 核 7G 的机器上同时有别的任务，内存见底时让
#     kernel 先杀自己，别误伤常驻的 qdrant 或旁边 agent 的进程；
#  2. python -u —— 进度日志（已写入/累计/ETA）实时落盘，被 kill 后能看到
#     死在哪一批（stderr 默认块缓冲，几小时的活儿会丢一整段尾巴）。
set -e
game="${1:?用法: tools/embed_resume.sh <game> [额外 embed_index.py 参数...]}"
shift
echo 1000 > /proc/self/oom_score_adj
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -u embed_index.py --game "$game" --append --backend onnx "$@"
