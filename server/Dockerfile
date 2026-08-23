# bili CLI（B 站匿名采集，已验证方案见 .agents/skills/anisignal/references/data-sources.md）
FROM ghcr.io/tamnd/bili:latest AS bili

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# bili 二进制位置随上游镜像可能变化，用 find 定位后拷入（scratch/distroless 都适用）
COPY --from=bili / /opt/bili-image/
RUN bin="$(find /opt/bili-image -type f -name bili -perm /111 | head -n 1)" \
    && [ -n "$bin" ] \
    && cp "$bin" /usr/local/bin/bili \
    && chmod +x /usr/local/bin/bili \
    && rm -rf /opt/bili-image

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
