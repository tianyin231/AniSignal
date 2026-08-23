# AniSignal

《鸣潮》跨平台事件雷达：把多平台原始内容归并为可追溯的事件，用户订阅后不再蹲评论区。

- 产品与系统设计：`docs/superpowers/specs/2026-08-12-wuthering-waves-event-radar-design.md`
- 项目专属 agent 指南（架构决策、已验证采集方案、合规红线）：`.agents/skills/anisignal/`
- 后端（FastAPI + arq worker + 调度器 + 采集器）：`server/`
- 公开站点与管理看板（Nuxt 4）：`web/`，管理看板在 `/admin`
- 部署：根目录 `docker compose up`（MySQL + Redis + api + scheduler + worker）

## 快速开始

```bash
# —— 本地看面板（零依赖，不需要 Docker）——
cd server && uv run python -m app.local    # API: 127.0.0.1:8000, Token: local-dev
cd web && npm install && npm run dev       # 打开 http://localhost:3000/admin

# —— 服务器全栈（需要 Docker）——
cp server/.env.example .env   # 并修改 ANISIGNAL_ADMIN_TOKEN
docker compose build
docker compose run --rm api alembic upgrade head   # 建表
docker compose up -d
```

后端本地开发与测试见 `server/README.md`。
