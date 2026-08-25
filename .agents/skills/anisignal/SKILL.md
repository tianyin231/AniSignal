---
name: anisignal
description: "AniSignal（鸣潮跨平台事件雷达）项目专属指南。在本仓库做任何工作之前先读本 skill：后端采集器、worker 任务队列、事件引擎、AI 层、管理员看板、MySQL 建模、Redis 消息、Nuxt 前端、docs 文档或 Docker 部署都适用。包含已确定的架构决策、已实测验证的数据源采集方案（B 站 bili-cli、米游社、库洛官网 CDN、YouTube RSS）、事件聚合与降噪规则、AI 摘要/翻译/用量记账、合规红线和仓库结构。凡是涉及'采集/抓取/爬虫/关键词/定时任务/事件/履约/早报/监控位/AI 摘要/LLM'的实现或修改，必须先读本 skill。"
---

# AniSignal 项目指南

《鸣潮》跨平台事件雷达：把多平台原始内容归并为**可追溯的事件**，用户订阅后不再蹲评论区。首版聚焦库洛游戏《鸣潮》+ 爱弥斯联名键盘的履约追踪，每天 08:00（北京时间）早报 + 重要状态变化即时邮件（通知未实现）。

完整产品与系统设计见 `docs/superpowers/specs/2026-08-12-wuthering-waves-event-radar-design.md`（约 530 行，涉及产品边界、领域模型、事件/证据规则时先读它）。本 skill 只保留动手时必须记住的结论。

## 系统现状（2026-08-23，运行于服务器 154.40.58.230 /www/AniSignal）

访问 `http://154.40.58.230:8090`；管理看板 `/admin`（管理员 Token 存于服务器 `.env`）；测试账号 `demo@anisignal.dev / anisignal123`。

| 部分 | 位置 | 状态 |
|---|---|---|
| 产品设计文档 | `docs/superpowers/specs/` | 已定稿，评审通过 |
| 后端 | `server/`（FastAPI + arq + SQLAlchemy 2 + Alembic，迁移至 0009） | 采集闭环 + 事件引擎 + AI 层在线运行中，75 个单测全绿 |
| Web 前端 | `web/`（Nuxt 4 + Tailwind 4） | 全站真实数据（首页要闻/内容库/事件/商品/来源/账号中心/认证页），管理看板 7 页 |
| 部署 | 根目录 `Dockerfile` + `compose.yaml` | 4 容器：mysql / redis / app（FastAPI+Nitro 同容器，对外仅 8090，`/api/**` 同源代理）/ collector（调度+worker 同容器，Redis 领导锁支持 `--scale collector=N`）。数据 bind mount 在 `./data/`；单镜像含 bili+python+node。服务器更新流程：`git bundle` 传输 → `git merge --ff-only` → `docker compose up -d --build`（注意：必须先 `git checkout -- .` 清工作区，否则 merge 静默失败） |

`server/` 关键路径：`app/services/classify.py`（规则分类+词典种子+HIDDEN_CATEGORIES 降噪）、`app/services/events.py`（聚合引擎）、`app/services/ai.py`（LLM 客户端/摘要/翻译/用量记账）、`app/services/views.py`（事件/商品序列化）、`app/services/collect.py`（编排+健康+filter_relevant 相关性过滤+关键词打标）、`app/services/ingest.py`（幂等入库+reparse）、`app/services/queue.py`（ArqQueue/LocalQueue）、`app/services/settings.py`（站点配置 KV）、`app/services/auth.py`（scrypt/会话/验证码/限频）、`app/collectors/`、`app/parsers/`、`app/api/`（public/admin/admin_ai/admin_settings/auth/account 路由）、`app/local.py`（**本地测试入口：`uv run python -m app.local`，SQLite+进程内队列，免 Docker**）。新平台接入 = collector + parser + 在 `ingest.PARSERS` 与 `collect._COLLECTORS` 各注册一行。

## 核心数据链路

```text
采集目标（admin 配置：关键词/账号/频率/共现词/热度门槛）
  → collector（平台适配器 + filter_relevant 降噪 + 关键词溯源打标）
  → ingest（幂等入库：raw_contents 不可覆盖 + content_versions 解析/分类可重算 + metric_snapshots 时间快照）
  → 聚合引擎（官方公告 72h+标题公共子串聚类合并；商品履约按 watch_terms 挂证据、独立账号数推进阶梯）
  → events（AI 摘要 + facts 关键事实 + revisions 修订史）
  → 公开 API → Nuxt SSR
```

三条不可违反的数据原则：**原始数据不可覆盖**（改解析产生新 version）；**AI 结果可以重算**（`POST /api/admin/reparse` 全量重算解析+分类+聚合；`POST /api/admin/ai/summarize-all` 补摘要）；**事件结论可以修正**（人工编辑自动锁定，引擎跳过 locked）。

## 降噪与信息分层（2026-08-23 上线）

- 公开内容流默认**隐藏闲聊与低价值推广类目**（chat/other/promotion：二创/同人/抽奖/签到/商城礼包…）；`include_chat=true` 仅用于管理与追溯查全量。
- 首页信息分层：**近期聚合结论 + 社区观察 > 官方原始内容**；内容按 `published_at` 排序（缺失时才回退 `collected_at`），禁止新回填的旧内容抢占顶部。事件按最新有效证据时间排序。
- 采集目标支持 `co_terms`（共现词：标题/简介须至少命中一个）与 `min_views`（热度门槛）——入口在管理看板采集目标表单。
- YouTube RSS 同时支持官方与精选社区频道；只有官方 channel ID 白名单标记为 official，社区频道必须配置 `co_terms` 避免混入其他游戏。
- 内容与事件均带 `matched_keywords`（采集关键词溯源，多词共现合并展示），前端显示为蓝色 chips。

## 事件引擎（规则版）

- 分类词典（classification_rules 表）管理端可编辑，改后需全量重算生效。
- 聚合规则：官方同类目+72h 内+标题公共子串 ≥4 字符才合并（宁缺勿滥，不同主题绝不合并——曾因"同类目唯一活跃事件可延续"兜底导致误合并，已删）；商品事件按 watch_terms 匹配标题。
- 履约阶梯（products.computed_stage）：送测样品→首位用户到货→多名独立用户到货（≥3 独立账号）→…；stage_hint 可手动覆盖（-1=自动）。
- 事件 facts 结构化关键事实（引擎模板生成，AI 摘要可覆盖）；置顶=importance（管理端事件列表 ☆ 按钮）。
- 已知边界：**跨语言公告不会自动合并**（中英标题无共享词，留给 AI 语义匹配）；B 站 DRAW 图文动态正文/图片 CLI 拿不到（列表与详情接口均空，bili 0.3.0 的动态接口被 -352 风控门挡住，匿名无解，页面有诚实的"查看原文"降级）。

## AI 层（2026-08-23 上线，管理端 `/admin/ai` 页）

- OpenAI 兼容接口（`site_settings('ai')` 优先，env `ANISIGNAL_AI_*` 兜底）：base_url/api_key/model/price_in/price_out（每百万 token 单价，算费用）。
- 当前配置：`apihub.agnes-ai.com/v1` + `agnes-2.5-flash`（免费），定价 0 是正确的。
- 任务：`summarize_event`（JSON 摘要+facts，写 revisions）、`translate_content`（kuro_global/youtube → zh-CN 存 content_translations）、`test`。聚合引擎变更事件后经 arq 自动投递摘要。
- **每次调用记 ai_usage 表**（task/tokens 明细/费用/耗时/状态）；`/admin/ai` 页有用量总览（窗口+累计，按任务分布）与最近调用明细。
- 未配置时任务安全跳过（skipped: ai_not_configured）。

## 账号系统（2026-08-23 上线）

- 邮箱+密码（scrypt 标准库），服务端会话（httpOnly cookie，改密/重置吊销全部会话）；验证码 6 位/15 分钟/5 次，发码与登录限频，防枚举文案。
- 用户关键词监控（user_keywords，10 个上限，启停切换）→「我的关注流」（标题命中，分页 20/页）；资料：昵称+12 个预设头像（渐变色卡，白名单校验，无紫色）。
- 路由：`/login /register /forgot /account`（auth 布局：极简品牌头+表单+真实数据预览栏）；登录/注册后进账号中心。
- 邮件：通用 SMTP 热配置（管理端站点设置页，含测试发信）；未配置时验证码落库，`GET /api/admin/email-codes` 可查。

## 站点配置（管理端「站点设置」页）

`site_settings` KV：smtp（DB 优先 env 兜底，密码脱敏/留空保留）、announcement（全服横幅 info/warning，所有布局）、texts（6 个文案区域白名单）。公开 `GET /api/site-config` 只暴露公告与文案。

## 采集实现速记（详细命令与字段见 references/data-sources.md）

动手写采集代码之前**必须先读 `references/data-sources.md**（唯一持久副本；.worktrees 里的原始笔记不被 git 跟踪）。速记：

- B 站走 bili CLI（0.2.0，镜像内 /usr/local/bin/bili）：`dynamics <mid>` 入口（不要 `user --dynamics`）；搜索→BV→`video` 详情；匿名链路偶发返回空，已加隔 3 秒重试。
- 鸣潮官方 UID `1955897084` 必须配置化。B 站图文动态（DRAW）无正文/图片是硬边界。
- kuro 公告列表包裹键是 `game`，tabTitle/tabBanner 多语言 dict，时间为毫秒（parser v2）。
- 动态标题推导：正文首行跳过 #话题#（parser v3）。

## 合规红线（设计文档 §13 + 实测边界）

- 只用**匿名、公开、只读**接口；不绕过登录/验证码/风控（如 B 站动态详情 -352 需登录——不做）；不伪造 Cookie。
- 低频+缓存+重试；平台礼貌间隔 Redis 控制。采集幂等（平台+外部ID 唯一键）。
- 内容详情页只保存必要信息（标题/正文/指标/图片直链），不镜像完整内容；外链为参考出处。
- 外部 HTML 一律 bleach 白名单消毒后再渲染（`_sanitize_html`，已有攻击向量测试）。

## 其他必须遵守的约定

- **时间**：存储 UTC（naive），展示北京时间；全站格式 `YYYY/MM/DD HH:mm`（`utils/admin-types.ts fmtTime`）。
- **MySQL**：utf8mb4；新表迁移必须给存量行回填默认值（0007 漏回填曾致 /api/admin/overview 500）。
- **前端**（web/README.md）：三栏论坛布局、macOS 玻璃材质、论坛蓝、无紫色系、mono 时间戳；身份标签色系见 data.ts identityMeta。页面文案走 site-config（`useSiteConfig().t(key)` 带默认值）。
- **管理端交互**：所有异步按钮必须有 busy 文案+toast（useAdminToast，右下角带时间戳）；禁止 alert()。
- **模板改动必须浏览器实测**（本会话两次因批处理脚本引入模板结构 bug——嵌套 `<a>`、灯箱插入错位——构建通过但渲染碎裂；验收用 DOM snapshot/截图，不能只看构建和状态码）。

## 部署速查（服务器 us3q / 154.40.58.230）

```bash
# 本地：提交推送 + 打包传输
git push origin main && git bundle create /tmp/a.bundle main && scp /tmp/a.bundle us3q:/www/
# 服务器
cd /www/AniSignal && git checkout -- . && git fetch /www/a.bundle main:s && git merge --ff-only s && git branch -D s && rm /www/a.bundle
docker compose up -d --build
# 迁移随 app 容器启动自动执行（deploy/start-app.sh 里 alembic upgrade head）
# 验证：curl http://127.0.0.1:8090/api/healthz；容器产物内容校验用中文界面字符串 grep（函数名会被压缩改名，不要用标识符搜）
```

## 未完成 / 下一步选项

- **邮件通知与早报**（设计文档 §10）：账号+SMTP+监控关键词都就绪，差发送管线与 08:00 调度。
- 跨语言公告 AI 语义合并；用户内容 LLM 相关性判定（替代关键词匹配）。
- HTTPS/域名（nginx 80 端口被其他服务占用，需所有者确认划分）。
- 支付/订阅（远期，设计文档阶段 C）。
