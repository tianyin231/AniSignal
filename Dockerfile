# 统一镜像：app（api+web）与 collector（调度+采集）共用
# 构建上下文为仓库根目录（compose.yaml 所在处）
FROM ghcr.io/tamnd/bili:latest AS bili

FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Node 运行时（Nitro SSR 产物只需要 node 可执行文件）
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node

# bili 二进制位置随上游镜像可能变化，用 find 定位后拷入
COPY --from=bili / /opt/bili-image/
RUN bin="$(find /opt/bili-image -type f -name bili -perm /111 | head -n 1)" \
    && [ -n "$bin" ] \
    && cp "$bin" /usr/local/bin/bili \
    && chmod +x /usr/local/bin/bili \
    && rm -rf /opt/bili-image

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev

COPY server/app ./app
COPY server/alembic.ini ./
COPY server/alembic ./alembic
COPY --from=webbuild /web/.output /app/web
COPY deploy/ ./deploy/
