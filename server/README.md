# AniSignal Server

FastAPI + arq 常驻 worker + 调度器 + 四平台采集器（B站 bili-cli / 米游社 / 库洛官网公告 / YouTube RSS）。技术决策与采集方案背景见 `.agents/skills/anisignal/`。

## 结构

```text
app/
├── api/          # FastAPI：/admin/*（Token 鉴权）+ /api/healthz
├── collectors/   # 平台采集器（B站 subprocess 调 bili 二进制，其余 httpx）
├── parsers/      # 原始 JSON → 统一 NormalizedContent schema（parser_version 版本化）
├── services/     # ingest 幂等入库 / collect 采集编排（健康状态、失败退避）
├── worker/       # arq 任务（常驻 worker 入口）
├── scheduler/    # 调度循环：扫 collect_targets 到期投递（±10% 抖动）
├── models/       # SQLAlchemy 模型（6 表）
└── schemas/      # Pydantic DTO
```

数据链路：`采集器 → 原始 JSON 落库(不可覆盖) → 解析 → content_versions(指纹变化才新版本) + metric_snapshots(时间快照)`。

## Docker 部署（推荐，服务器 Linux）

```bash
cd 仓库根目录
cp server/.env.example .env          # 修改 ANISIGNAL_ADMIN_TOKEN
docker compose build
docker compose run --rm api alembic upgrade head
docker compose up -d                 # mysql + redis + api + scheduler + worker

# 冒烟：创建「鸣潮」关键词搜索目标并手动触发
curl -X POST localhost:8000/admin/collect-targets \
  -H "X-Admin-Token: $ANISIGNAL_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"platform":"bilibili","target_type":"keyword_search","target_value":"鸣潮"}'
curl -X POST localhost:8000/admin/collect-targets/1/run -H "X-Admin-Token: $ANISIGNAL_ADMIN_TOKEN"
curl "localhost:8000/admin/contents" -H "X-Admin-Token: $ANISIGNAL_ADMIN_TOKEN"
```

注意：B 站链路依赖 Linux 版 bili 二进制（镜像内已含），Windows 本机不能直跑 bilibili 采集，请在容器/服务器验证。

## 本地测试（推荐入口，Windows 可直接跑）

```bash
cd server
uv run python -m app.local
```

零依赖跑通面板全链路：SQLite（`local.db`，自动建表）+ 进程内任务执行（不需要 MySQL / Redis / Docker），API 在 `http://127.0.0.1:8000`（Token 默认 `local-dev`，控制台会打印）。首次启动自动播种「鸣潮官网公告（真实数据，开箱即采）」和「B站搜索·鸣潮（停用，本机无 Linux bili 二进制，手动触发会体现为失败健康态）」。

配合前端看板：

```bash
cd web && npm install && npm run dev   # 打开 http://localhost:3000/admin，Token 填 local-dev
```

## 本地开发（除 B 站采集外均可直跑）

```bash
cd server
uv sync
uv run pytest                      # 全部单测（SQLite 内存库，无需 MySQL）
uv run uvicorn app.api.main:app --reload   # 需要 ANISIGNAL_* 环境变量（见 .env.example）
uv run python -m app.scheduler.loop        # 调度器（arq/Redis 模式）
uv run arq app.worker.settings.WorkerSettings  # worker（arq/Redis 模式）
```

## 环境变量

前缀 `ANISIGNAL_`（见 `app/config.py`）：`DATABASE_URL`、`REDIS_URL`、`ADMIN_TOKEN`（必须改）、`WUTHERING_WAVES_BILI_MID`（鸣潮官方 UID，禁止硬编码）等。

## 首版边界

事件引擎、AI 翻译/摘要、通知邮件、用户订阅未实现（`app/worker/settings.py` 的 `ai_placeholder` 为占位）；采集遵守匿名公开只读原则，不绕过登录/验证码/风控（项目 skill 合规红线）。
