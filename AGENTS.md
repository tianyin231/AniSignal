# AGENTS.md

AniSignal 是轻量自托管的跨平台事件雷达与情报信号台（支持游戏分区、AI 快讯、RSS 订阅、通用网页监测与定制监控）。本文件面向所有 AI 编码工具与协作者，只保留动手改代码时**必须遵守的通用硬约束与校验命令**；私有主机、部署路径与个人实例配置严禁写入本仓库。

## 架构速览

- **后端**（`server/`）：Python 3.12+、FastAPI、SQLAlchemy 2（async）、Alembic、arq + Redis；本地免 Docker 入口为 `app/local.py`（SQLite + 进程内队列）。
  - 核心分层：`app/collectors/`（平台采集）→ `app/parsers/`（结构化解析）→ `app/services/ingest.py`（幂等入库与版本化）→ `app/services/events.py` / `news_editorial.py`（规则归并 / AI 资讯编辑流）→ `app/api/`（公开 / 账号 / 管理端 / 只读 MCP）。
  - 新增采集平台：实现 collector + parser，并在 `app/services/ingest.py`（`PARSERS`）与 `app/services/collect.py`（`_COLLECTORS`）注册，同步接入前端 `web/app/utils/lexicon.ts`。
- **前端**（`web/`）：Nuxt 4、Tailwind CSS 4、TypeScript；Nitro 运行时将 `/api/**` 反代至后端，`NUXT_API_SSR_BASE` 控制 SSR 目标。
- **插件**（`integrations/`）：独立于主服务的聊天机器人与联动插件。
- **部署**（`compose.yaml`）：5 容器单职责形态（`mysql:8.4`、`redis:7`、`api`、`web`、`collector`），共用同一预构建镜像，以 2~4 GB 内存轻量自托管为基线。

## 必跑验证命令

在提交或宣布任务完成前，按改动范围执行对应确定性校验（用工具兜底，不靠肉眼猜测）：

```bash
# 后端全量单测（含迁移与模型一致性检查）
uv run --directory server pytest -q

# 前端工具单测与生产构建
npm --prefix web test
npm --prefix web run build

# 本地免 Docker 启动（后端 :8000 / 前端 :3000）
uv run --directory server python -m app.local
npm --prefix web run dev
```

## Git 提交规范

- 遵循 **Conventional Commits**：`<type>(<scope>): <简明中文标题>`（冒号前 `type` / `scope` 用英文，冒号后**标题必须为中文**、末尾不加句号；常用 type：`feat`、`fix`、`refactor`、`perf`、`docs`、`test`、`chore`）。
- **必须附带面向访客的中文正文描述**：前端「开源与更新」（`/project`）会直接展示提交标题，并在「展开详情」中以**纯文本**原样渲染提交正文。标题下方空一行，用清晰、无 raw Markdown 标记（不用 `**` / `` ` ``）的中文条目说明本次功能变化或修复点，避免堆砌内部文件名或泄露私有运维信息（详见 [CONTRIBUTING.md](CONTRIBUTING.md)）。
- 单次提交聚焦单一主题，不夹带无关格式化改动、构建产物、本地 `*.db` 或 `.env`。

## 不可违反的工程与安全红线

1. **原始数据不可覆盖**：采集入库幂等，新解析结果、网页变更与人工修订均追加 `content_versions`；AI 产出必须可重算；人工编辑后的事件自动锁定，规则引擎跳过 `locked` 事件。
2. **轻量自托管与依赖红线**：**禁止向主镜像引入常驻 Chromium / Playwright / Puppeteer 等无头浏览器**；AI（LLM）与平台 Token 均为可选增强，未配置时采集、正文 Diff 与基础推送仍需正常运行。
3. **安全与隐私**：
   - 严禁向仓库提交真实 Cookie、API Key、管理员 Token、服务器 IP、私人域名、生产部署路径或未公开邮箱；示例链接与页面文案中不得出现管理凭据。
   - 所有出站用户/管理员 URL 必须经过公网地址与重定向逐跳 DNS/SSRF 校验（复用 `app/services/urls.py`、`app/collectors/rss.py`、`app/services/webhooks.py`）。
   - 外部 HTML 一律经 `bleach` 白名单消毒后再渲染；公开接口全量过滤 `raw_contents.hidden=true`；事件跨分区永不合并。
4. **数据库与时间规范**：
   - 时间统一存 naive UTC，展示为北京时间 `YYYY/MM/DD HH:mm`（复用 `web/app/utils/admin-types.ts` `fmtTime` 与 `schedule.ts`）。
   - 修改 `server/app/models/` 必须同步编写 Alembic 迁移；迁移脚本须同时兼容 SQLite 测试（列修改使用 `op.batch_alter_table`）与空 MySQL 8.4 的 `alembic upgrade head && alembic check`，存量行须回填默认值，且不得硬编码个人站点种子数据。
   - 时间窗口相关单元测试一律使用相对当前时间（如 `utcnow() - timedelta(days=1)`），禁止写死会随日期漂移的固定时间戳。

## 前端、文案与验收约束

1. **文案与术语单一真相源**：严格遵守 [docs/ui-copy-guidelines.md](docs/ui-copy-guidelines.md)——使用克制、准确的书面产品语（既不泄露工程黑话，也不过度口语化）。所有平台名、监控类型、目标类型、收录策略与后台分组导航统一从 [`web/app/utils/lexicon.ts`](web/app/utils/lexicon.ts) 导出，**禁止在页面组件内重复维护映射字典**。
2. **信号台视觉与克制原则**：
   - 颜色仅使用 `web/app/assets/css/main.css` 定义的 `--sd-*` 语义变量对应 Tailwind 工具类，禁止硬编码破坏深浅主题切换的色值。
   - **禁止无意义的灰色解释性填充小字**：状态优先用视觉载体表达，决策副标仅出现在创建/选择节点。
   - **渐进披露与条件渲染**：表单主路径收敛在 2~3 个核心字段，低频配置归入折叠 `高级选项`；平台不适用字段直接条件不渲染，禁止放禁用占位框；卡片/表格行常驻操作控制在 1~2 个高频项，其余进 `⋯` 菜单或抽屉。
   - **随机壁纸必须保留**：开启壁纸时 `<html data-wallpaper>` 触发毛玻璃效果，新增浮于背景的标题/导航条须加 `.wall-surface` 并在壁纸与深浅模式下核对可读性。
   - 管理端所有异步操作必须提供忙碌状态与 `useAdminToast()` 提示，禁止 `alert()`。
3. **模板改动必须浏览器实测**：涉及 Vue 模板、样式或交互的修改，`npm run build` 通过不能代替验收；必须在真实浏览器检查渲染结构、响应式防折行、深浅模式与控制台零报错。

## 按需查阅文档（Progressive Disclosure）

不要在无关任务中预读全部文档，按任务范围按需打开：

- **贡献、Git 提交与 PR 规范**：[CONTRIBUTING.md](CONTRIBUTING.md)
- **文案规范与高密度界面减负细则**：[docs/ui-copy-guidelines.md](docs/ui-copy-guidelines.md)
- **后端 / 数据库 / 采集 / 前端 / 插件避坑备忘**：[docs/engineering-notes.md](docs/engineering-notes.md)
- **前端结构与设计令牌说明**：[web/README.md](web/README.md)
- **功能边界与各链路实现参考**：[docs/features.md](docs/features.md)
- **通用信源与周期报告演进路线**：[docs/plans/2026-11-25-general-sources-and-reports.md](docs/plans/2026-11-25-general-sources-and-reports.md)
- **容器化部署说明**：[docs/deployment.md](docs/deployment.md)
