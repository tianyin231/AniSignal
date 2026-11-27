# 通用信源拓展与报告推送演进规划

- **日期**：2026-11-25（2026-11-27 更新：已完成 Phase 1、Phase 2、Phase 3.1 与 Phase 3.3 通用周期报告）
- **关联 Issue**：[#10 方向建议：建议不局限于游戏资讯，信源拓展和采集方式拓展](https://github.com/tianyin231/AniSignal/issues/10)
- **状态**：Phase 1 / Phase 2 / Phase 3.1 / Phase 3.3 已落地，剩余 Phase 3.2 自然语言语义过滤待排期

---

## 1. 背景与目标

AniSignal 当前已经形成稳定的底层数据链路：

```text
多平台采集（collector）→ 幂等入库与版本化（raw_contents / content_versions）
  → 规则/AI 归并、版本 Diff、摘要翻译
  → 多端触达（Web / 邮件 / Webhook / QQ Bot / RSS / MCP）
```

社区在 #10 中提出了非常有价值的演进方向：希望不局限于游戏资讯，能够把这套「自定义分区 + 多源采集 + 降噪归并 + 主动推送」的底座拓展到**热门开源项目进展、官网/活动页变更、非关键词语义关注以及周期性报告推送**。

本规划基于仓库现有架构与资源约束，梳理出可平滑落地、低维护成本的分期实施路线，供后续按需推进。

---

## 2. 架构约束与设计红线

在拓展通用能力时，以下核心边界保持不变：

1. **轻量自托管优先**：整套服务仍以 2~4 GB 内存为基线，**不向主镜像引入常驻 Chromium / Playwright 无头浏览器**，避免容器内存暴涨与反爬维护泥潭。
2. **渐进式依赖**：大模型（LLM）与平台 Token 均为*可选增强*；未配置 AI 时，采集、正文 Diff、结构化清单推送仍可正常运行。
3. **合规与安全红线**：
   - 只调用公开只读接口、原生 Atom/RSS 或标准 HTTP 页面，不做登录绕过、验证码破解或私有签名逆向；
   - 所有用户/管理员输入的出站 URL 必须经过公网地址校验与重定向逐跳 DNS/SSRF 检查（复用 `app.collectors.rss.public_feed_url` 与 `app.services.webhooks.ensure_public_webhook_target`）。
4. **原始数据不可覆盖**：新版本解析、页面变更均追加 `ContentVersion`，保留追溯与 Diff 能力。
5. **零破坏兼容**：现有 14 个游戏分区（`domain="game"`）、AI 快讯（`domain="news"`）与小黑盒独立分区（`domain="feed"`）的行为不受影响。

---

## 3. 现有可复用底座

仓库内已经具备大部分底层组件，无需重复造轮子：

| 已有能力 | 代码位置 | 可直接复用场景 |
| :--- | :--- | :--- |
| HTML 正文抽取（`trafilatura>=2.0`）与 SSRF 防护 | `server/app/services/rss_body.py`、`server/app/collectors/rss.py` | 自定义官网正文抓取与降噪 |
| 内容版本 Diff 与修订通知 | `server/app/services/content_diff.py`、`server/app/services/revision_notifications.py` | 官网单页内容变更对比与推送 |
| 7 种通用 Webhook 推送（飞书/钉钉/企微/Discord/Bark/ntfy/JSON） | `server/app/services/webhooks.py` | 项目进展日报/周报、网页变更即时推 |
| 日报/周报/月报数据结构与引用校验 | `server/app/services/news_reports.py`、`server/app/services/news_publication.py` | 泛化为通用分区/自定义监控周期报告 |
| 非游戏主题流隔离（跳过游戏词典/履约/日历/首页聚合） | `server/app/communities.py`（`domain="feed"`） | 开源项目/技术追踪等独立分区 |
| 共享采集 + 用户级过滤定制监控 | `server/app/services/custom_feeds.py` | 开放给普通用户订阅 GitHub 仓库或网页 |
| 只读 Streamable HTTP MCP 服务端 | `server/app/services/mcp_tools.py` | 与外部 AI Agent 协同生成深度研究报告 |
| GitHub 公开 API 读取参考 | `server/app/services/project_github.py` | 复用 Releases / Commits 字段结构 |

---

## 4. 分期演进方案

### Phase 1：开发者平台信源（GitHub）✅ 已落地并按收束规划收敛（2026-11-27）

**目标**：聚焦「热门开源项目趋势刊物 + 仓库版本发布追踪」核心场景，坚持**默认匿名可用、零数据库迁移**，同步接入后台采集目标（`/admin/targets`）与用户定制监控（`/account`）。

> 注：2026-11-27 按架构收束规划（决策 A2），移除了低频的 GitCode 平台及 GitHub Issues / Discussions 采集分支，将原千行 `git_repo.py` 拆分为 `github.py`（Releases 与采集器）与 `github_trending.py`（Trending 榜单与匿名 README 抓取）。

#### 1.1 平台与目标类型
- 平台：`github`（GitHub）。
- 目标值规范化（`normalize_github_repo`）：支持输入 `owner/repo` 或公开 GitHub 仓库链接（自动剥离 `.git`、`/releases` 等尾部路径并校验域名），统一归一化为 `owner/repo`；用户定制监控按小写折叠共享单一 `CustomFeedSource`。
- 目标类型：
  1. `releases`（后台）/ `gh_releases`（定制监控）：追踪正式版与预发布（prerelease）、版本号与 Release Notes，支持可选关闭预发布过滤；
  2. `trending`（后台，GitHub）：采集 `github.com/trending` 的 `daily` / `weekly` / `monthly` 公开静态榜单，落库 `git_trending_editions` 按期归档日刊/周刊/月刊，自动计算跨期去重与上榜标记（首次上榜 / 连榜 N 期 / 复榜 / 跨榜提示），并在主题流页面提供专属刊物阅读台。

#### 1.2 匿名优先的采集策略与限额保护
- **GitHub Releases**：
  - 优先调用 `GET https://api.github.com/repos/{owner}/{repo}/releases`，持久化 `ETag` 配合 `If-None-Match` 实现无更新时 `304 Not Modified` 零配额消耗；
  - 匿名额度触发 `403/429` 限流或上游故障时，自动回退至无配额限制的原生 Atom 源 `https://github.com/{owner}/{repo}/releases.atom`（同样带 `ETag`）；
  - REST API 与 Atom 回退共用基于 `{owner}/{repo}:release:{tag_name}` 的稳定 `external_id`，来回切换绝不重复入库，修改 Release Notes 自动追加 `ContentVersion` 并联动 Diff 修订通知。
- **可选提额令牌**：保留可选环境变量 `ANISIGNAL_GITHUB_TOKEN`（默认留空即纯匿名，重度实例按需通过 `.env` 配置）。

#### 1.3 已落地模块
- `server/app/collectors/github.py`、`server/app/collectors/github_trending.py`、`server/app/parsers/github.py`：Releases 采集器、Trending 榜单与匿名 README 抓取及内容解析
- `server/app/models/enums.py`、`server/app/schemas/content.py`、`server/app/schemas/api.py`、`server/app/models/collect_target.py`：平台/目标类型/内容类型枚举与 `repo_config` 校验
- `server/app/services/collect.py`、`server/app/services/ingest.py`、`server/app/services/custom_feeds.py`、`server/app/services/custom_policy.py`、`server/app/api/admin.py`、`server/app/api/account.py`、`server/app/api/admin_custom.py`、`server/app/api/custom.py`：后台目标与共享定制监控全链路接入
- `web/app/utils/lexicon.ts`、`web/app/utils/custom-feeds.ts`、`web/app/pages/admin/targets.vue`、`web/app/components/account/Monitors.vue`、`web/app/pages/admin/customs.vue`、`web/app/pages/custom/`：平台徽章、「开源项目」选源分组与按需折叠的高级选项

---

### Phase 2：轻量自定义网页监控（`web_page`）✅ 已落地（2026-11-26）

**目标**：覆盖没有 RSS 的官网公告页、产品 Changelog、定价页、活动专题页，直接复用现有正文抽取与版本 Diff 链路。

#### 2.1 通用开源解析栈（`server/app/services/web_parser.py`）
在不引入无头浏览器的前提下，组合以下纯 Python / `lxml` 生态开源库实现零样板通用解析：
- **`parsel`**（Scrapy，BSD-3-Clause）：CSS3 / XPath 选择器精准限定与排除区域裁剪；
- **`trafilatura` + `htmldate` + `courlan`**（Apache-2.0）：主流文章正文、元信息、发布/修订时间提取与列表 URL 规范化去噪（剔除 `utm_*`、锚点及导航/登录链接）；
- **`readability-lxml`**（Apache-2.0）：Mozilla Readability 兜底，自动补全 `trafilatura` 在短公告与价格/版本表格页上的漏提；
- **`html2text`**（GPL-3.0，与 `urlwatch`/`webchanges` 同源）：将裁剪或兜底后的 HTML 确定性转换为可读文本/Markdown 表格，适配逐行 Diff。

#### 2.2 两种监控模式（同步打通 `/admin/targets` 与 `/account` 定制监控）
1. **单页变更监控（`page_watch`）**：
   - 定时请求目标公开 URL（携带 `ETag` / `Last-Modified`，`304` 直接跳过；规则变更自动失效旧缓存）；
   - 支持可选 CSS/XPath 内容选择器、排除选择器与忽略动态行规则（子串或 `regex:`），未填选择器时自动级联多引擎抽取；
   - **后台采集目标**：首次入库 `v1`，正文实质变更追加新 `ContentVersion`，自动联动 `/contents/{id}#revisions` 可视化 Diff 与官方修订通知（邮件 / Webhook / 个人 RSS / Bot）；
   - **用户定制监控**：同一 URL 共享单次抓取，按用户各自选择器隔离快照，首次基线快照免发邮件，后续实质变更自动生成带 Diff 摘录的新条目并支持邮件推送。
2. **列表页新增发现（`list_watch`）**：
   - 自动吸收 JSON-LD（`ItemList`/`BlogPosting`/`NewsArticle`）与语义主区链接，支持可选区域选择器与链接前缀/正则匹配（如 `/blog/`、`regex:/news/2026`）；
   - 仅对新发现文章并发补读正文与精确日期，已有条目跳过抓取。
3. **测试解析预览（`POST /api/admin/web-page/preview` & `/api/account/web-page/preview`）**：
   - 保存前一键实测解析效果，自动探测站点 `<link rel="alternate">` 官方 RSS/Atom（支持一键改用 RSS 订阅）并识别空壳 SPA 站点给出提醒。

#### 2.3 降噪与安全边界
- **去抖降噪**：内置脚本/样式/隐藏节点清洗、可选 `exclude_selector` 与 `ignore_patterns` 行级过滤，结合 `content_diff` 实质变动判定防止无意义噪音反复告警。
- **安全与限额**：
  - 复用 `public_feed_url` 逐跳 DNS/IP 校验阻断内网/回环/云元数据 SSRF，限制单页最大 5 MB、重定向 ≤ 6 次、拒绝二进制资源；
  - 个人定制监控默认最小间隔 ≥ 60 分钟，单用户配额沿用 `custom_feed_policy` 统一管控，预览接口按账号限频；迁移 `0064` 将定制监控目标列拓宽至 255 字符。
- **能力边界**：专注静态 HTML / SSR 官网（覆盖大多数开源项目官网、文档、博客）；重度前端渲染 SPA 或强盾站点不在主站硬扛，建议通过上游 RSSHub / Changedetection 转 RSS 接入。

---

### Phase 3：非游戏主题流解耦、语义筛选与通用周期报告

**目标**：让自定义非游戏分区真正具备「AI 提炼 + 意图过滤 + 结构化报告推送」闭环，而不只是原始条目列表。

#### 3.1 `domain="feed"` 主题流按需增强 ✅ 已落地（2026-11-27）
- 在 `site_settings("communities")` 中新增分区级 `ai_summary` 开关（管理端「站点分区」页可按分区启停；GitHub / GitCode 开源项目与版本发布在 `domain="feed"` 下默认启用，小黑盒等中文社区默认关闭零额外开销）；
- 新增 `content_briefs` 表（迁移 `0066`）与慢 AI 队列任务 `brief_content`：对开源仓库匿名抓取 README 精读正文（零 API 配额消耗），生成中文定位标题、一句话用途、详细摘要、核心亮点、适用场景与主题标签；
- 升级所有 `domain="feed"` 主题流页面为双栏信号台（深色频道头图 + 多图图文卡片/紧凑双视图 + 话题筛选 + 近期热门/热门话题/来源状态侧栏），趋势榜单改为整卡直入站内 AI 解读与 README 精读详情页，并保留外链快捷直达按钮。
- `include_in_digest: bool`（纳入个人早报/周期报告）归入 3.3 周期报告一并推进。

> 说明：现有 `domain="news"` 的编辑流（去重/双盲评分/成刊）多处硬编码 `community == "ai"`（`services/ai_news.py`、`services/news_reports.py`）。若未来需要第二个独立行业新闻流，再将硬编码的 `"ai"` 泛化为 `domain == "news"`，现阶段无需大改。

#### 3.2 自然语言语义过滤（替代死板关键词匹配）
- 为采集目标（`/admin/targets`）与定制监控（`/account`）增加可选字段 **`intent_prompt`**（例如：*“关注大模型推理框架的新版本与破坏性变更，忽略入门教程和求助帖”*）；
- 采集入库前/审核阶段，对命中目标的新内容交由后台异步队列的轻量模型做二元相关性判定与简短理由记录（复用 `curation` 并发控制与 AI 密钥池）；
- 未配置 AI 时安全降级回退到常规关键词/共现词规则，不阻断采集。

#### 3.3 通用定时报告（日报 / 周报）+ Webhook 推送 ✅ 已落地（2026-11-27）
- 新增 `periodic_reports` 与 `report_subscriptions` 两张表（迁移 `0067`），同分区同刊期报告只生成一次并跨订阅者复用，用户定制监控合集报告严格私有隔离；
- **有 AI 模式**（按任务分层 `periodic_report`）：按主题聚类输出「重点进展 + 版本变更 + 值得关注」结构化报告，所有链接严格回填自库内候选杜绝幻觉；
- **无 AI 降级模式**：按来源与内容类型确定性分组输出精简标题、指标与原文链接清单；
- **多端送达与站内阅读**：
  - 升级现有 7 种 `UserWebhook`（飞书卡片 / 钉钉与企微 Markdown / Discord Embed / Bark / ntfy / 通用 JSON）支持 `periodic_report` 报告格式，并新增「仅接收周期报告」通道模式；
  - 同步支持精排 HTML 邮件与北京时间 08:30 定时调度（日报每日、周报每周一），新增 `/reports` 报告归档与 `/reports/[id]` 精读页（支持一键复制 Markdown）及账号中心「周期报告推送」订阅管理；
  - 同步补齐 `domain="feed"` 分区 `include_in_digest` 开关，管理端可按分区决定是否纳入全站早报汇总。

---

## 5. 明确暂不纳入范围（Non-Goals）

为防止架构失控，以下方向明确**不在主站核心内实现**：

1. **内置无头浏览器爬虫（Playwright / Puppeteer / Selenium）**：内存占用与维护成本超出轻量自托管定位；复杂动态站由外部工具转 RSS 接入更合理。
2. **自主联网搜索型 Deep Research Agent**：服务端不内置开放式全网搜索循环（token 成本不可控且容易产生不可追溯幻觉）；深度调研场景应由外部 Agent 通过内置只读 **MCP Server（`/mcp`）** 消费 AniSignal 数据来完成。
3. **带登录态的私有仓库/付费墙采集**：坚持公开只读源定位，避免凭据托管与合规风险。

---

## 6. 建议实施顺序

1. **Phase 1（GitHub / GitCode Releases）**：改动集中、验证快、零新基础设施，最直接回应 #10 关注热门项目进展的需求。
2. **Phase 2（`web_page` 轻量单页变更监控）**：充分复用 `trafilatura` 与版本 Diff，低成本补齐无 RSS 官网场景。
3. **Phase 3（`feed` AI 摘要开关 → 通用周报/日报推 Webhook → 自然语言语义过滤）**：逐步补完报告形式与非关键词关注能力。
