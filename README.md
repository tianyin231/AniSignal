# AniSignal

《鸣潮》跨平台事件雷达：把多平台原始内容归并为可追溯的事件，用户订阅后不再蹲评论区。

**线上**：`http://154.40.58.230:8090`（公开站）· `/admin` 管理看板 · `/login` 用户登录

- 产品与系统设计：`docs/superpowers/specs/2026-08-12-wuthering-waves-event-radar-design.md`
- 项目专属 agent 指南（**新会话必读**：架构决策、数据链路、降噪规则、AI 层、部署速查、已知边界）：`.agents/skills/anisignal/SKILL.md`
- 已验证数据源方案（B 站 bili-cli / 米游社 / 库洛官网 CDN / YouTube RSS，含字段与陷阱）：`.agents/skills/anisignal/references/data-sources.md`
- 后端（FastAPI + arq worker + 事件引擎 + AI 层）：`server/`（75 个单测）
- 公开站点与管理看板（Nuxt 4 + Tailwind 4）：`web/`

## 功能总览（2026-08-23）

| 模块 | 状态 |
|---|---|
| 四平台采集（B站/米游社/官网国服+国际服/YouTube RSS） | ✅ 运行中，关键词溯源+共现词/热度降噪 |
| 事件引擎 v1（公告聚类合并 / 商品履约阶梯 / 证据链 / 修订史） | ✅ 规则版在线 |
| AI 层（事件摘要 / 外语翻译 / 用量记账） | ✅ OpenAI 兼容接口，管理端 `/admin/ai` 可视化用量与费用 |
| 账号系统（邮箱验证 / 改密找回 / 关键词监控 / 关注流 / 头像昵称） | ✅ |
| 管理看板（采集目标 / 事件审核+置顶 / 商品 / 分类词典 / 内容 / 站点设置 / AI 层） | ✅ 全部真实操作 |
| 站内内容详情（解析正文/消毒/指标历史/媒体灯箱/关联事件） | ✅ |
| 邮件通知与早报 | ⏳ 未实现（SMTP/账号/监控词已就绪） |

## 部署（4 容器）

mysql / redis / **app**（FastAPI+Nitro 同容器，对外仅 8090，`/api/**` 同源代理）/ **collector**（调度+worker，Redis 领导锁，可 `--scale collector=N`）。数据 bind mount 在 `./data/`。

```bash
# —— 本地看面板（零依赖，不需要 Docker）——
cd server && uv run python -m app.local    # API: 127.0.0.1:8000, Token: local-dev
cd web && npm install && npm run dev       # http://localhost:3000

# —— 服务器全栈（需要 Docker）——
cp .env.example .env    # 修改 ANISIGNAL_ADMIN_TOKEN
docker compose up -d --build    # 建表随启动自动执行
```

服务器代码更新走 `git bundle`（私有仓库无服务器凭据）+ `docker compose up -d --build`，完整流程见 skill「部署速查」。后端测试：`cd server && uv run pytest`。
