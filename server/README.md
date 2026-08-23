# AniSignal Server

FastAPI + arq 常驻 worker + 事件聚合引擎 + AI 层。架构决策与数据链路背景见 `.agents/skills/anisignal/SKILL.md`（新会话必读）。

## 结构

```text
app/
├── api/           # 路由：public（/api/*）admin（/api/admin/*）admin_ai admin_settings auth account
├── collectors/    # 平台采集器（bilibili 走 bili CLI subprocess；其余 httpx）
├── parsers/       # 原始 JSON → 统一 NormalizedContent（parser_version 版本化）
├── services/
│   ├── classify.py    # 规则分类（词典种子 + HIDDEN_CATEGORIES 降噪）
│   ├── events.py      # 聚合引擎（公告聚类 / 商品履约 / facts 模板）
│   ├── ai.py          # LLM 客户端 / 事件摘要 / 翻译 / ai_usage 记账
│   ├── ingest.py      # 幂等入库 + reparse 全量重算
│   ├── collect.py     # 采集编排 + filter_relevant 降噪 + 关键词打标
│   ├── settings.py    # 站点配置 KV（smtp/公告/文案）
│   ├── auth.py        # scrypt 密码 / 会话 / 验证码 / 限频
│   └── queue.py       # ArqQueue / LocalQueue
├── scheduler/     # 调度循环（Redis 领导锁，多副本安全）
├── worker/        # arq 任务（collect_target / summarize_event / translate_content）
└── models/        # 9 次迁移后的全部表
```

数据链路：`采集 → filter_relevant 降噪 → 幂等入库（raw 不可覆盖）→ 分类 → 聚合成事件 → AI 摘要 → 公开 API`。全量重算入口：`POST /api/admin/reparse`（解析+分类+聚合）与 `POST /api/admin/ai/summarize-all`（摘要）。

## 本地测试（推荐，免 Docker）

```bash
cd server
uv run python -m app.local
# SQLite + 进程内队列；API 127.0.0.1:8000，Token local-dev，自动播种演示采集目标
```

## 测试与开发

```bash
uv run pytest                       # 75 个单测（SQLite 内存库）
uv run uvicorn app.api.main:app --reload
uv run arq app.worker.settings.WorkerSettings   # Redis 模式 worker
```

## 迁移

Alembic 迁移随 app 容器启动自动执行（`deploy/start-app.sh`）。手写新迁移时**必须给存量行回填默认值**（0007 漏回填曾致管理端 500）。

## 环境变量（前缀 ANISIGNAL_，见 app/config.py）

`ADMIN_TOKEN`（必改）、`DATABASE_URL`、`REDIS_URL`、`AI_BASE_URL/AI_API_KEY/AI_MODEL`（管理端配置优先）、`SMTP_*`、`WUTHERING_WAVES_BILI_MID`（鸣潮官方 UID，禁止硬编码）。

## 范围外（未实现）

邮件通知与早报（SMTP/账号/监控词已就绪）、跨语言公告 AI 语义合并、支付订阅。
