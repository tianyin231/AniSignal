# 统一镜像：api、web 与 collector 共用同一预构建产物（单镜像、按容器职责拆分单进程运行）
# 构建上下文为仓库根目录（compose.yaml 所在处）
FROM ghcr.io/tamnd/bili:0.3.0 AS bili

FROM --platform=$BUILDPLATFORM node:22-slim AS webbuild
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --replace-registry-host=always --registry=${NPM_REGISTRY}
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS pybuild
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /usr/local/bin/uv
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# python:3.12-slim 已内置 ca-certificates 与 libstdc++6，仅做存在性校验，避免无意义的 apt 网络请求
RUN dpkg -s ca-certificates libstdc++6 >/dev/null

# Node 运行时（Nitro SSR 产物只需要 node 可执行文件）
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=bili /usr/bin/bili /usr/local/bin/bili
COPY --from=pybuild /app/.venv /app/.venv

COPY server/app ./app
COPY server/alembic.ini ./
COPY server/alembic ./alembic
COPY --from=webbuild /web/.output /app/web
COPY deploy/*.sh ./deploy/
RUN sed -i 's/\r$//' ./deploy/*.sh && chmod +x ./deploy/*.sh
