#!/bin/bash
# app 容器入口：FastAPI（内部 8000）+ Nitro SSR（3000，唯一对外端口）
# 任一进程退出即容器退出，交给 restart 策略拉起；启动时自动跑迁移
set -e

alembic upgrade head

uvicorn app.api.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!

node web/server/index.mjs &
WEB_PID=$!

trap 'kill "$API_PID" "$WEB_PID" 2>/dev/null' TERM INT
wait -n "$API_PID" "$WEB_PID"
exit $?
