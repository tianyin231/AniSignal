#!/bin/bash
# api 容器入口：启动时自动执行数据库迁移（MySQL GET_LOCK 互斥），随后以前台单进程运行 FastAPI
set -e

if ! alembic upgrade head; then
  echo "==================================================================" >&2
  echo "[AniSignal] 数据库迁移失败，API 服务未启动。" >&2
  echo "请执行 'docker compose logs api' 查看上方具体异常堆栈，" >&2
  echo "排查方法见 docs/deployment.md（首次安装失败 / 迁移排障）。" >&2
  echo "==================================================================" >&2
  exit 1
fi

exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
