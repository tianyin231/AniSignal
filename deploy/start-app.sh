#!/bin/bash
# app 容器入口：FastAPI（内部 8000）+ Nitro SSR（3000，唯一对外端口）
# 任一进程退出即优雅停止另一进程并退出容器，交给 restart 策略拉起；启动时自动执行数据库迁移
set -e

if ! alembic upgrade head; then
  echo "==================================================================" >&2
  echo "[AniSignal] 数据库迁移失败，服务未启动。" >&2
  echo "请执行 'docker compose logs app' 查看上方具体异常堆栈，" >&2
  echo "排查方法见 docs/deployment.md（首次安装失败 / 迁移排障）。" >&2
  echo "==================================================================" >&2
  exit 1
fi

uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1 &
API_PID=$!

node web/server/index.mjs &
WEB_PID=$!

shutdown() {
  kill "$API_PID" "$WEB_PID" 2>/dev/null || true
  wait "$API_PID" "$WEB_PID" 2>/dev/null || true
}

trap 'shutdown; exit 0' TERM INT

set +e
wait -n "$API_PID" "$WEB_PID"
CODE=$?
shutdown
exit "$CODE"
