# Contributing to AniSignal

感谢参与改进或接手维护 AniSignal！动手开发与提交 PR 前请先阅读以下通用规范。

## 1. 本地开发环境

需要 **Python 3.12+、uv、Node.js 22 与 npm**（本地开发无需安装 MySQL、Redis 或 Docker）：

```bash
# 后端（SQLite + 进程内队列，默认管理令牌 local-dev）
cd server
uv run python -m app.local

# 前端（http://localhost:3000，自动代理 /api/** 到 :8000）
cd web
npm ci
npm run dev
```

## 2. Git 分支与提交规范（Conventional Commits）

仓库采用 **Conventional Commits**，一次提交聚焦单一主题。前端「开源与更新」（`/project`）会直接读取仓库最近的提交记录：首行作为更新标题展示，空行后的正文会在「展开详情」中以**纯文本**原样呈现给站点访客。

```text
<type>(<scope>): <简明中文标题>

- <面向用户或维护者的中文更新说明 1>
- <面向用户或维护者的中文更新说明 2>
```

- **标题要求**：
  - 冒号前 `type` 与可选 `scope` 保持英文，**冒号后的标题必须为中文**、动宾短语优先、末尾不加句号。
  - 常用 `type`：`feat`（新功能/新平台）、`fix`（缺陷修复）、`refactor`（重构/文案整理）、`perf`（性能优化）、`docs`（文档/规范）、`test`（测试）、`chore`（依赖/构建/迁移工具/版本号）。
  - 常用 `scope`（可选）：`web`、`web_page`、`calendar`、`codes`、`diff`、`search`、`alembic`、`infra`、`mcp`、`plugins`、`subscriptions` 等。
- **正文描述要求（直接展示在 `/project`「展开详情」）**：
  - 提交时必须空一行补写**中文正文描述**，用简明条目写清本次新增能力、交互改进或修复内容。
  - 页面以纯文本 `<pre>` 展示正文，**不要使用 Markdown 语法**（如 `**加粗**`、反引号、链接语法），少堆内部代码文件名与测试流水，严禁包含任何私有运维信息。
- **示例**：
  ```text
  feat(web_page): 新增通用网页监测与解析预览能力

  - 支持单页变更与列表更新两种网页监测模式，后台与个人定制监控同步可用
  - 新增测试解析预览，自动探测目标站点官方 RSS 并支持一键切换订阅
  - 接入逐跳公网地址校验与噪音过滤规则，仅采集公开静态网页内容
  ```
- **提交与 PR 纪律**：
  - 不在同一次提交里混杂无关格式化改动；不要提交构建产物、本地数据库（`*.db`）或 `.env`。
  - 涉及界面/交互调整的 PR 请附带修改前后的页面验证说明（含浅色/暗黑模式及壁纸模式核对）。

## 3. 代码、前端设计与文档地图

不同领域的详细约定按职责拆分，按需查阅：

- **AI 编码通用约束**：[AGENTS.md](AGENTS.md)
- **前端文案、术语与高密度界面规范**：[docs/ui-copy-guidelines.md](docs/ui-copy-guidelines.md)（平台、监控类型与策略词条统一由 `web/app/utils/lexicon.ts` 导出，禁止在页面内重复维护字典）
- **前端结构与信号台设计令牌**：[web/README.md](web/README.md)（仅使用 `--sd-*` 语义变量、保留随机壁纸与 `.wall-surface` 可读性）
- **通用工程避坑与插件协议备忘**：[docs/engineering-notes.md](docs/engineering-notes.md)（SQLAlchemy / Alembic / 采集器 / Nuxt / AstrBot 插件开发的现行技术边界）
- **功能边界与容器部署说明**：[docs/features.md](docs/features.md) · [docs/deployment.md](docs/deployment.md)

## 4. 提交前必检清单

1. **运行确定性测试与构建**：
   ```bash
   uv run --directory server pytest -q
   npm --prefix web test
   npm --prefix web run build
   ```
   > 涉及 Vue 模板、样式或交互的修改，构建通过不能替代浏览器实测，请在本地浏览器核对排版、深浅模式与控制台零报错。
2. **数据库变更**：
   - 修改 `server/app/models/` 须同步编写 Alembic 迁移脚本（`server/alembic/versions/`），兼顾 SQLite 单测（列修改使用 `op.batch_alter_table`）与空 MySQL 8.4 的 `alembic upgrade head && alembic check`（CI 会自动校验）。
   - 新增非空或枚举列须回填存量行默认值，不得在迁移脚本中硬编码个人站点种子数据。
3. **隐私与安全**：
   - 严禁向仓库提交真实 Cookie、API Key、管理员 Token、服务器 IP、私人域名、部署容器名或未公开邮箱。
   - 新增采集平台或外部 URL 处理须经过现有公网地址与逐跳重定向 SSRF 校验（`app/services/urls.py`、`app/collectors/rss.py`、`app/services/webhooks.py`）；外部 HTML 一律经 `bleach` 消毒。
   - **禁止向主镜像引入常驻 Chromium / Playwright / Puppeteer 等无头浏览器**，保持 2~4 GB 内存轻量自托管基线。
4. **最小完整改动**：
   - 复用仓库既有分层与组件，不引入无调用方的抽象或多余依赖；非平凡逻辑与时间窗口边界须附带相对时间单测。

## 5. 许可证

提交至本仓库的代码默认遵循项目根目录 [LICENSE](LICENSE)（GNU AGPL v3.0）。
