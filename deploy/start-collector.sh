#!/bin/bash
# collector 容器入口：调度器 + 快 IO worker + 慢 AI worker 同容器隔离运行
# 扩采集/AI 能力：docker compose up -d --scale collector=2
# 调度器有 Redis 领导锁，多副本不会重复投递；快/慢任务经独立 Redis 队列隔离分发
set -e

python -m app.scheduler.loop &
SCHED_PID=$!

arq app.worker.settings.FastWorkerSettings &
FAST_WORKER_PID=$!

arq app.worker.settings.AiWorkerSettings &
AI_WORKER_PID=$!

shutdown() {
  kill "$SCHED_PID" "$FAST_WORKER_PID" "$AI_WORKER_PID" 2>/dev/null || true
  wait "$SCHED_PID" "$FAST_WORKER_PID" "$AI_WORKER_PID" 2>/dev/null || true
}

trap 'shutdown; exit 0' TERM INT

set +e
wait -n "$SCHED_PID" "$FAST_WORKER_PID" "$AI_WORKER_PID"
CODE=$?
shutdown
exit "$CODE"
