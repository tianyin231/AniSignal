#!/bin/bash
# collector 容器入口：调度器 + arq 采集 worker 同容器
# 扩采集能力：docker compose up -d --scale collector=2
# 调度器有 Redis 领导锁，多副本不会重复投递；任务经 Redis 队列分发
set -e

python -m app.scheduler.loop &
SCHED_PID=$!

arq app.worker.settings.WorkerSettings &
WORKER_PID=$!

trap 'kill "$SCHED_PID" "$WORKER_PID" 2>/dev/null' TERM INT
wait -n "$SCHED_PID" "$WORKER_PID"
exit $?
