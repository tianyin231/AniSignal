#!/bin/bash
# app 容器入口：FastAPI（内部 8000）+ Nitro SSR（3000，唯一对外端口）
# 任一进程退出即容器退出，交给 restart 策略拉起；启动时自动跑迁移
set -e

# 迁移偶发撞锁（部署时新旧容器衔接）会自愈，重试 3 次避免陷入崩溃循环
alembic_ok=0
for attempt in 1 2 3; do
  if alembic upgrade head; then
    alembic_ok=1
    break
  fi
  echo "alembic attempt $attempt failed, retrying in 10s"
  sleep 10
done
[ "$alembic_ok" = "1" ] || exit 1

uvicorn app.api.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!

node web/server/index.mjs &
WEB_PID=$!

trap 'kill "$API_PID" "$WEB_PID" 2>/dev/null' TERM INT
wait -n "$API_PID" "$WEB_PID"
exit $?
