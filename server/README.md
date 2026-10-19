# AniSignal Server

[产品首页](../README.md) · [功能指南](../docs/features.md) · [部署指南](../docs/deployment.md)

信号台的采集、归并与送达层。FastAPI、SQLAlchemy、Alembic 与 arq 负责接口、模型、迁移和后台任务；完整部署使用 MySQL 与 Redis，本地模式使用 SQLite 与进程内队列。

## 主要模块

| 路径 | 职责 |
| --- | --- |
| `app/api/` | 公开、账号与管理员 API。 |
| `app/collectors/`、`app/parsers/` | 平台采集与统一内容解析；B 站采集依赖 `bili` CLI。 |
| `app/services/collect.py`、`ingest.py` | 采集编排、降噪、来源状态、幂等入库及解析版本。 |
| `app/services/events.py` | 规则事件归并、证据关联与人工修正保护。 |
| `app/services/ai.py`、`curation.py` | 可选摘要、翻译、内容筛选及用量记录。 |
| `app/services/ai_news.py`、`app/api/ai_news.py` | AI 快讯的审核准入、主题筛选、来源追溯与公开读取。 |
| `app/services/custom_feeds.py` | 用户 B 站 UID／关键词定制监控，使用独立条目存储。 |
| `app/services/notifications.py`、`bot.py` | 邮件早报、手动通知、投递记录与可选 AstrBot 集成。 |
| `app/scheduler/`、`app/worker/` | 定时调度与后台任务。 |
| `app/communities.py`、`app/models/` | 分区元数据与数据模型。 |

主数据链路为 `采集目标 → 筛选 → 版本化入库 → 分类与事件 → 公开 API / 关注 / 通知`。定制监控走独立链路，不进入主内容库及事件引擎。

## 本地运行与测试

从仓库根目录进入 `server`，需要 Python 3.12+ 和 uv：

```bash
cd server
uv run python -m app.local
```

API 监听 `127.0.0.1:8000`，本地 Token 默认为 `local-dev`。入口会创建 SQLite 数据库并按条件播种演示目标；部分目标会访问真实来源，B 站示例默认停用。完整说明见[本地开发](../docs/deployment.md#本地开发免-mysql--redis--docker)。

在 `server` 目录运行测试：

```bash
uv run pytest
```

不要将历史测试数量或旧版本通过结果当作本次验证。生产 worker 入口为 `app.worker.settings.WorkerSettings`；完整运行方式以根目录 Compose 和 `deploy/` 脚本为准。

## 变更约定

原始内容、解析版本和事件结论分层维护；修正应保留可追溯记录。人工锁定的事件不能被自动流程覆盖，未核验的商品候选信号不能计入公开结论。新增分区或平台时，要同步注册、校验、适配器与必要的数据迁移。

迁移位于 `alembic/`，完整部署在应用启动时执行 `alembic upgrade head`。新增字段时应考虑存量回填；SQLite 本地模式不代替 MySQL 迁移验证。

配置定义见 [app/config.py](app/config.py)。SMTP、AI 和机器人均需要额外服务配置；邮件早报与定制监控已有任务实现，是否启用取决于部署配置。当前事件归并仍以规则为主，不宣称跨语言 AI 语义合并。

[项目维护参考](../.agents/skills/anisignal/SKILL.md)与[早期设计](../docs/superpowers/specs/2026-08-12-wuthering-waves-event-radar-design.md)保留历史背景；旧日期的功能状态、主题范围与运维示例应以当前代码和部署配置复核。
