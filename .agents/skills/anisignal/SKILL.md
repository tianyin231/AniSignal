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
| 后端 | `server/`（FastAPI + arq + SQLAlchemy 2 + Alembic） | 采集闭环 + 事件引擎 + AI 层 + 邮件通知在线运行中，210 个单测全绿 |
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

三条不可违反的数据原则：**原始数据不可覆盖**（改解析产生新 version；管理端人工修订也产生新版本）；**AI 结果可以重算**（`POST /api/admin/reparse` 全量重算解析+分类+聚合；`POST /api/admin/ai/summarize-all` 补摘要）；**事件结论可以修正**（人工编辑自动锁定，引擎跳过 locked）。

## 游戏分区（2026-09-02 上线；2026-09-05 完成分区化改造，鸣潮/碧蓝航线）

- **分区注册表 `server/app/communities.py` 是唯一权威**（2026-09-05）：`COMMUNITIES` 每分区一条
  CommunityMeta（label/game_name/bare_name/game_note/other_games_examples/color/bili_mid_setting/
  official_bili_mid_default）。**新增分区 = 注册表加一条 + enums.py Community 加成员（测试断言一致） +
  config 加 `<game>_bili_mid`（可选）+ 需要历史内容时写迁移回填**，业务代码不用动。
- 由注册表派生的东西（不要在业务代码里写 `if community == "xxx"`）：全部 422 校验正则
  （`community_pattern()`，此前 11 处硬编码）、审核提示词（curation._review_system_prompt）、
  摘要提示词 game/game_note（ai.summarize_event）、摘要防串扰词表（`crosstalk_names()` =
  其他分区游戏名 + 固定 extras；本分区名不在名单，联动内容证据提及即放行）、
  标题去噪 `_TITLE_BOILERPLATE`（`game_name_boilerplate()`）、B 站官方 mid 集合
  （parsers/bilibili.py 遍历注册表读 config）。早报 AI 提示词是跨分区通用文案（关注流跨分区）。
- **community 字段**：collect_targets / raw_contents / events / products / product_signals（迁移 0025，
  信号显式携带商品分区，sync 时强制覆盖）/ classification_rules（可空=NULL 全分区通用）。
  内容 community **首次入库时由采集目标写入，之后不翻转**；事件跨分区永不合并
  （`_aggregate_official` 按分区切分输入、`_candidate_pairs` 跨分区不配对）；商品履约只在同分区
  内容池匹配 watch_terms。公开内容条目（PublicContentItem）也带 community 字段。
- 公开 API（/contents /events /products /highlights /sources /official-links）都带 `community` 查询
  参数，**默认 wuwa**，非法值 422；official-links 缓存按分区各存一份。`_load_known_terms` 词表只加载
  本分区目标关键词 + 通用规则。
- **批量重审**：`POST /api/admin/ai/review-community?community=X&limit≤500`（清该分区 content_reviews
  后按 50/块入队）；管理端内容页选分区筛选后出现「重审本分区」按钮。2026-09-05 用它修正了 blhx
  存量 84+ 条按 wuwa 语境误判"与鸣潮无关"而下架的内容——分区归属/提示词调整后必须重审存量。
- **前端视觉与信息架构（2026-09-05 三改定稿：频道式导航）**：顶栏品牌旁是**频道组**
  「首页 / 鸣潮 / 碧蓝航线 / …」（下划线选中态：选中项分区色文字+2px 下划线，无底色容器，
  类组在 COMMUNITY_CLASSES.nav）。**首页频道 = 跨分区聚合**（`/` 无 community 参数 → 后端以
  community=all 查询），分区频道 = `/?community=key`（显式写参数，与聚合首页区分；点击调
  useGameSection.selectCommunity 会写 localStorage）。**吸顶只有顶栏一层**；站点栏目导航（官方动态/商品/事件/内容库，带 communityQuery）放在
  首页内容区首行（panel-glass 磨砂条，与刷新同处一行，随内容滚动、不吸顶）。分区指示不带通栏强调条（2026-09-05 评估后删除：频道下划线已足够表达），
  悬停一律纯文字变色（容器内不放灰底 hover 块）。类组全部字面量写在 COMMUNITY_CLASSES（动态拼接会被 Tailwind purge）。
- **系统级工具页**：侧栏"数据统计/来源健康/订阅中心"不跟随顶栏分区——statistics 本就无分区过滤；
  sources 接受 `community=all`（页面固定拉 all 并逐行带分区徽章）；订阅中心是静态页。
- **聚合查询与聚合视角**：/api/contents、/api/events、/api/highlights、/api/products、/api/sources 接受
  `community=all`（`COMMUNITY_AGGREGATE_QUERY`；sources/official-links 仍严格单分区）。
  栏目页（事件/内容库/官方动态/商品）与右栏履约卡统一用 `useCommunityScope()` 解析视角：
  URL 带 community=all（首页"首页"频道进入）→ 跨分区取数 + 逐行/逐卡分区徽章 +
  平台 tab 用全分区并集 + 顶栏频道不高亮；分区首页进栏目则锁定该分区。
  聚合内容行（RawFeedRow `show-community`）、事件行、轮播卡都带分区徽章。
  `useGameSection.ts` 仍是分区唯一状态源（URL query 优先 → localStorage → 默认 wuwa），集中
  `PLATFORMS_BY_COMMUNITY`（平台筛选随分区收敛：blhx 只有 B站）。
- **首页信息架构（2026-09-05 定稿）**：官方精选轮播 → 近期聚合结论（5 条速览）→ 「最新·最热」
  混合流：官方+社区每页 15:30 双请求，页内按"有效时间"排序（官方 +36h、播放≥1 万 +12h），
  RawFeedRow 播放量≥1 万自动带 🔥热门徽章与播放数。右栏履约信号卡只做粗略速览（三轴状态行+待核验
  数），细节在商品详情页。
  **各数据页 key/参数/watch 都要带 community**（auth 布局事件预览曾是漏网）。跨分区场景（首页聚合、
  账号中心关注流）逐条显示分区徽章。
- 管理端：分区选项/徽章单一来源 `web/app/utils/admin-communities.ts`（targets/contents/events/
  products/rules 五页共用）；内容管理分区筛选+徽章+批量重审按钮；商品管理分区筛选+徽章；
  目标表单分区下拉；商品表单分区下拉（自动建的 B 站监控继承商品分区）；分类词典可限定分区。
- 坑：`_review_candidates` 里对 SQLAlchemy Result 只能迭代一次，分区词表分组前先 `.all()` 物化
  （曾因此 candidates=0 全部漏审）。

## 内容准入分级（2026-08-30）

- 采集目标带 `tier` 分级（迁移 0023，存量回填 general）：**core = 特定关键词**（商品/联名/周边，
  豁免共现词；min_views > 0 时仍作为兜底门槛）；**general = 泛关键词**（走共现词 + min_views 热度门槛，
  在 `filter_relevant` 采集侧硬过滤）。官方 notices 目标永不过滤。
  管理端目标表单分级选择前置、泛关键词默认门槛 10000、列表带播放门槛列（分级徽章 nowrap 防竖排）。
- 现行策略：鸣潮泛关键词 target min_views=10000；爱弥斯联名键盘 target=core。
  多社区扩展时按社区建目标组，每个社区的"商品/特定词"目标设 core、泛话题目标设 general+门槛。
- 管理端适配：内容管理页每行带来源分级徽章（核心来源/泛关键词/无匹配，按 matched_keywords ∩ core 目标词表判定）与分级筛选；
  采集目标页底部有「最近采集运行」区块（抓取→过滤→入库计数，`/api/admin/runs` 已含 items_fetched/items_filtered）。
- 分级筛选走**全量扫描（2000 上限）+ Python 过滤分页**（SQL offset 会漏配行、total 不准）；目标行的分级徽章**可点击切换** core/general（PUT tier）。
- 坑：目标**更新端点曾忽略 min_views/co_terms**（schema 有字段但端点不应用），已修——
  改采集过滤策略时先确认端点真的持久化这些字段。
- 坑：内容管理列表的审核筛选条件引用 ContentReview 但查询缺 JOIN → SQLAlchemy 隐式笛卡尔积，
  筛选返回全部内容且 total 膨胀（曾 62866）。**where 里引用的表必须在查询里显式 JOIN**。
- 管理端列表筛选交互为显式「查询」按钮应用（不随下拉变更即时搜索），翻页即时生效。

## 履约信号 AI 预判与候选可见（2026-08-30）

- 审核管线对命中商品 watch_terms 的内容追加**履约预判**（`content_reviews.fulfillment`，迁移 0021/0022）：
  kind ∈ self_purchase/pr_sample/forward/none + confidence + reason。
- 小模型（agnes-2.5-flash）长指令下**常丢尾部的 fulfillment 字段**：解析侧在 product_context 存在且字段缺失时，
  从 category_hint（unboxing/arrived）与理由正向词（自购/订单优先于送测/样品，注意"非官方送测"否定语境）确定性推导，置信 60。
  kind 别名映射（unboxing/arrived→self_purchase 等）也在解析层。
- 信号列表（`list_product_signals`）带 `ai_hint`：管理端候选按 AI 建议排序（自购开箱置顶）+ 预判徽章；
  公开侧候选信号标注"AI 预判（仍需人工核验）"。核验工作流本身仍是人工（products 管理页逐条确认）。
- 质量轴新增候选关注态：**≥2 个独立来源的未核验质量候选 → `candidate_attention`「多账号反馈质量问题」**（单来源不触发）。
- 三轴快照带 `ai_preview`：无核验数据时聚合候选的 AI 预判（如"5 条自购开箱候选（依据…）"），
  履约信号卡在对应轴下以蓝色"AI 预判"行展示。侧栏履约信号卡支持多商品 chips 切换（当前单商品不显示）。
- 商品列表卡带 `latest_candidate`（最新候选摘要+未核验标注）；右栏履约信号卡展示最新 2 条候选。
- 教训：改迁移前先 `git log` 检查并行会话是否也加了迁移（曾撞号 0021 双 head 导致站点重启循环）。

## 官方外链聚合（2026-08-30）

- `GET /api/official-links`：扫描近 30 天官方身份内容正文里的 http(s) 链接，剔除图片/静态资源主机（hdslb 等），按 host 分类（kuro/bilibili/youtube/miyoushe/web），去重取最新 30 条；进程内缓存 5 分钟。
- 管理端内容治理动作（下架/删除/修订）会调用 `invalidate_official_links_cache()` 立即失效缓存。
- 模板链接过滤：同 URL 在扫描窗口出现 ≥3 次（每条官方内容都带的下载页/官网/社媒签名块）剔除；黑名单直接排除——社媒（Facebook/Twitter/Discord/Reddit/TikTok/Instagram/频道页）+ **网盘与 UGC 作品页**（百度/夸克/迅雷网盘、lanzou、DeviantArt、Pixiv、ArtStation，2026-09-05 增补：曾因此混入用户资源帖/二创作者主页链接刷屏官方面板）。**AI 提取路径同样必须过滤官方身份**（`_ai_official_links` 曾漏掉 identity 过滤，用户内容被 AI 提取的链接全量混入面板——两路径共享 Source.identity=official 过滤）。
- 不可读标签（官网英文页锚文本 "information"、裸域名等 `_LINK_GENERIC_LABELS`）回退为**来源内容标题**；AI 路径的 title 字段填来源内容标题，前端 tooltip 显示"标题 · 链接名"（相同则去重）。
- 链接名称两级来源：**AI 审核时顺带提取**（`content_reviews.links`，简体中文名称如"B站主题收藏集"，面板优先用）；未审核内容回退规则提取（锚文本/前文短语）。审核完成会自动失效外链缓存。规则回退会跳过 AI 已提取过的内容。
- 侧栏「工具」与「官方链接」是两个独立面板。
- 前端 `OfficialLinks.vue` 双形态：桌面在侧栏下方列表（variant=list），移动端（lg 以下）在首页 tab 行下方横滑条（variant=strip）；`useOfficialLinks` 共享状态，首处触发拉取。

## 内容审核门禁（2026-08-30）

- 每次采集后对所有新入库内容**逐条多模态 AI 审核**（`review_contents`，标题+正文+封面图，Semaphore(4) 并发，AI 配置走 site_settings('ai')）；判定 relevant/promotional/violation/quality_score 存 `content_reviews`。
- **违规（violation）→ 自动 `raw_contents.hidden=true`；不相关（relevant=false）→ 仅非官方内容自动下架**（官方账号豁免相关性下架——新角色/新活动常超出模型知识库）。相关性判据不靠模型记忆：提示词注入管理员词表（采集目标关键词+分类词典，`_load_known_terms`）与内容的 matched_keywords，命中词表即相关，明确禁止以"不认识"判无关；管理员在词典补录新角色名即可让审核跟上版本更新。AI 失败保持可见（fail-open）。公开侧全量过滤 hidden：内容流/详情(404)/精选/事件证据(views.events_to_out)/账号关注流/邮件候选；事件聚合本身保留，仅证据列表过滤。
- 管理端内容页（/admin/contents「内容管理」）：审核徽章（违规/不相关/AI 分数+理由）、筛选（平台/审核结果/仅看已下架/标题搜索）、操作=下架|恢复（PATCH visibility）+ 修订（PATCH，产生新版本）+ 删除（DELETE，FK 级联，事件保留）+ 重审（POST review，清旧判定入队，队列抽象 `enqueue_review`）。
- 判定要克制是 prompt 明文要求；恢复后重新审核仍会按新判定再次下架。

## 降噪与信息分层（2026-08-23 上线）

- 公开内容流默认**隐藏闲聊与低价值推广类目**（chat/other/promotion：二创/同人/抽奖/签到/商城礼包…）；`include_chat=true` 仅用于管理与追溯查全量。
- 首页信息分层：**近期聚合结论 + 社区观察 > 官方原始内容**；内容按 `published_at` 排序（缺失时才回退 `collected_at`），禁止新回填的旧内容抢占顶部。事件按最新有效证据时间排序。
- 采集目标支持 `co_terms`（共现词：标题/简介须至少命中一个）与 `min_views`（热度门槛）——入口在管理看板采集目标表单。
- YouTube RSS 同时支持官方与精选社区频道；只有官方 channel ID 白名单标记为 official，社区频道必须配置 `co_terms` 避免混入其他游戏。
- 内容与事件均带 `matched_keywords`（采集关键词溯源）；蓝色 chips 只在内容行展示，事件行 2026-08-30 起精简为 身份+来源账号+平台徽章+功能状态（类目/地区/证据数/可信度/关键词 chips 已移除，详情页仍可见）。

## 事件引擎（规则版）

- 分类词典（classification_rules 表）管理端可编辑，改后需全量重算生效。
- 聚合规则：官方同类目+72h 内+标题公共子串 ≥4 字符才合并（宁缺勿滥，不同主题绝不合并——曾因"同类目唯一活跃事件可延续"兜底导致误合并，已删）；商品事件按 watch_terms 匹配标题。
- 履约阶梯（products.computed_stage）：送测样品→首位用户到货→多名独立用户到货（≥3 独立账号）→…；stage_hint 可手动覆盖（-1=自动）。
- 事件 facts 结构化关键事实（引擎模板生成，AI 摘要可覆盖）；置顶=importance（管理端事件列表 ☆ 按钮）。
- 已知边界：**跨语言公告不会自动合并**（中英标题无共享词，留给 AI 语义匹配）。B 站 DRAW 图文动态的正文/图片走匿名公开详情补全（`_enrich_draw_dynamics`），B 站风控瞬时拒绝时会有空动态入库——**自愈回填** `_backfill_empty_draw_dynamics` 会在后续动态采集时重试近 7 天的空动态（每轮最多 5 条），大部分能补回；个别被彻底拒绝的保持"查看原文"降级。

## AI 层（2026-08-23 上线，管理端 `/admin/ai` 页）

- OpenAI 兼容接口（`site_settings('ai')` 优先，env `ANISIGNAL_AI_*` 兜底）：base_url/api_key/model/price_in/price_out（每百万 token 单价，算费用）。
- 当前配置：`apihub.agnes-ai.com/v1` + `agnes-2.5-flash`（免费），定价 0 是正确的。
- 任务：`summarize_event`（JSON 摘要+facts，写 revisions）、`translate_content`（kuro_global/youtube → zh-CN 存 content_translations）、`test`。聚合引擎变更事件后经 arq 自动投递摘要。
- **每次调用记 ai_usage 表**（task/tokens 明细/费用/耗时/状态）；`/admin/ai` 页有用量总览（窗口+累计，按任务分布）与最近调用明细。
- 未配置时任务安全跳过（skipped: ai_not_configured）。

## 账号系统（2026-08-23 上线，账号中心 2026-08-29 整理）

- 邮箱+密码（scrypt 标准库），服务端会话（httpOnly cookie，改密/重置吊销全部会话）；验证码 6 位/15 分钟/5 次，发码与登录限频，防枚举文案。**登录用户必然已验证**（register/login/重置均不建未验证会话），`/account` 不需要未验证引导 UI。
- 用户关键词监控（user_keywords，10 个上限，启停切换）→「我的关注流」（标题命中，分页 20/页）；资料：昵称+12 个预设头像（渐变色卡，白名单校验，无紫色）。
- 账号中心（2026-08-29 整理）：不展示订阅（未上线）；关键词 chip 带命中数（`GET /api/account/keywords` 返回 `matches`，union_all 单查询）且**点击 chip 可筛选关注流**（`GET /api/account/feed?keyword_id=`，条目 `matched_keywords` 标注命中的用户关键词，响应含 `new_today` 近 24h 采集数）；改密默认折叠。登录页/账号页按钮在 IAB 自动化里 click 可能因 actionability 超时，可用 `form.requestSubmit()` 兜底。
- 站点背景可选随机壁纸（2026-08-29）：`GET /api/wallpaper` 返回 JSON `{url}`（解析 api.yppp.net 最终原图地址，每次刷新新图、2s 去重防抖），前端**图床 CDN 直连**显示。**不要把壁纸字节引到本站出口**：实测用户到图床 CDN ~4MB/s、到本站仅 ~180KB/s，服务端压缩转发（试过 Pillow→WebP+LQIP）反而慢数倍，已回退只留解析+下载代理；`/wallpaper/download?url=` 只转发 15min 内签发过的地址（内存签发集，防开放代理），保证下载的就是当前展示那张。前端 `useWallpaper`（localStorage `anisignal-wallpaper`，与主题同级）；顶栏 `WallpaperToggle` 按钮**以 status 为 key 重挂载**（否则 PROPS-only patchFlag 按钮的子节点不更新、图标不渲染）；失败自动回退默认极光背景。覆盖 default/account/auth 布局，管理看板不接壁纸。
- 路由：`/login /register /forgot /account`（auth 布局：极简品牌头+表单+真实数据预览栏）；登录/注册后进账号中心。
- 邮件：通用 SMTP 热配置（管理端站点设置页，含测试发信）；未配置时验证码落库，`GET /api/admin/email-codes` 可查。

## 站点配置（管理端「站点设置」页）

`site_settings` KV：smtp（DB 优先 env 兜底，密码脱敏/留空保留）、announcement（全服横幅 info/warning，所有布局）、texts（6 个文案区域白名单）。公开 `GET /api/site-config` 只暴露公告与文案。

## 采集实现速记（详细命令与字段见 references/data-sources.md）

动手写采集代码之前**必须先读 `references/data-sources.md**（唯一持久副本；.worktrees 里的原始笔记不被 git 跟踪）。速记：

- B 站走 bili CLI（0.2.0，镜像内 /usr/local/bin/bili）：`dynamics <mid>` 入口（不要 `user --dynamics`）；搜索→BV→`video` 详情；匿名链路偶发返回空，已加隔 3 秒重试。
- **B 站 `video` 详情端点风控门禁 + 熔断（2026-09-12）**：服务器 IP 对匿名 `video` 详情被持续拦截
  （stderr 含 "risk control ... supply a logged-in cookie"；搜索/动态端点不受影响）。**不配 BILI_COOKIE**
  （红线），改为：`BiliRiskControlError` 风控拒绝立即失败不重试（collectors/bilibili.py `_run_once`
  检测 stderr 标记）+ 进程级按命令熔断（`_CIRCUIT_STATE` 模块级 dict——collector 每任务新建实例，
  状态必须放模块级；连续 2 次拒绝熔断该命令 30 分钟，成功复位，冷却到期后下次调用自然探测）。
  熔断期间 `search_videos` 直接用搜索结果兜底：**实测搜索响应自带 view/reply/danmaku/favorite**
  （仅 coin/share/like/duration 为 0），min_views 门槛过滤与 🔥热门徽章不受影响。
  部署首日实测：每次搜索任务详情请求从 ~60 次（20 视频×3 重试+退避）降到 2 次、任务耗时从 ~2 分钟降到 <1 秒。
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
- **前端**（web/README.md）：三栏论坛布局、macOS 玻璃材质、论坛蓝、无紫色系、mono 时间戳；身份标签色系见 data.ts identityMeta。页面文案走 site-config（`useSiteConfig().t(key)` 带默认值）。品牌图标是 Q 版看板娘（`web/public/icon.png`，favicon 32px / apple-touch-icon 180px 同源图），顶栏与 account/auth 布局品牌位用 `<img src="/icon.png">`，favicon 配置在 nuxt.config `app.head.link`。
- **管理端交互**：所有异步按钮必须有 busy 文案+toast（useAdminToast，右下角带时间戳）；禁止 alert()。
- **禁止解释性填充小字**（2026-09-05 用户明确要求）：吸顶导航/列表区不放"××按分区独立组织""当前分区：××"
  这类灰色说明 caption——无价值小字加大信息密度、显得低级没有活人感；状态信息用视觉载体表达
  （分区=频道下划线+强调条颜色，功能=可点击的控件），文字只出现在有操作或数据语义的地方。
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

## 分区扩容与品牌色（2026-09-06）

- **四分区主题色对齐游戏品牌**（useGameSection COMMUNITY_CLASSES + admin-communities BADGE）：
  鸣潮=yellow（黑金）、碧蓝航线=sky（碧蓝）、崩坏星穹铁道=violet（紫）、明日方舟=orange（方舟橙）。
  'blue' 保留给非分区频道（首页/定制化）与兜底。改色只动 COMMUNITY_CLASSES/ADMIN_COMMUNITY_BADGE。
- **明日方舟分区（arknights，迁移 0029）**：B站官方 mid 161775300（bili CLI 核验认证"明日方舟官方
  账号"，config arknights_bili_mid）；YouTube EN 官方 UCR0J2NYGuC8epsa1O4DMmXQ（handle 页 canonical +
  externalId 双确认；**YouTube RSS 对服务器 IP 有间歇性 404/500 风控，与已知模式一致，会自愈**）；
  0029 播种官方动态(core)/社区搜索(明日方舟, min_views 10000)/YouTube 官方/Rexlent·Xlice 社区
  (co_terms 明日方舟)。
- contents.vue 的分区短徽章用 ADMIN_COMMUNITY_SHORT 查表（三元硬编码在 4 分区下会错标）。

## 管理端用户管理（2026-09-05 上线）

- **模型**：users.status（active/banned，0003 已有）+ banned_reason/banned_at（迁移 0026，附
  ix_users_status 索引）。封禁在三层强制执行：登录/重置 403（api/auth.py）、会话解析直接失效
  （get_user_by_token 查 status）、自动通知跳过（process_delivery）。
- **API（api/admin_users.py，/api/admin/users）**：GET 列表（邮箱/昵称 like + 状态/验证筛选 +
  id 倒序分页 + 关键词数分组子查询，按 1 万用户规模设计）；GET {id} 详情（关键词订阅 + 最近投递）；
  PATCH {banned/ban_reason/notification_enabled}（封禁即时 revoke_all_sessions）；POST {id}/email
  单发；POST /broadcast 广播（默认仅已验证+活跃，confirm 位必填，SMTP 未配置 400 快速失败，
  50 人/块入队 send_admin_emails worker 任务，上限 2 万，dedupe_key=broadcast:{batch}:{uid}）；
  GET deliveries/recent 触达审计。**注意 FastAPI 对 Query 默认空串也校验 pattern**——可空筛选
  正则要写成 `^(|a|b)$`（曾因 `^(a|b)$` + default "" 全列表 422）。
- **前端 /admin/users**：搜索/筛选/分页表单、状态徽章、通知订阅行内开关、详情面板（关键词 chips
  + 投递记录）、单发邮件表单、广播编辑器。投递行 kind=admin/broadcast/morning/ai。
- **轮播卡排版（2026-09-05）**：标题 text-lg + line-clamp-3（中文标题 20-30 字，窄栏 2 行 clamp
  会截掉一半关键信息）；卡片 md:h-80；摘要素 line-clamp-2。注意 Tailwind v4 的 line-clamp 在新版
  Chromium 计算为 flow-root 但省略号仍正常渲染（实测验证过）。
- **KURO_ITEM 等固定时间戳测试样例对时间窗口测试有边界漂移风险**（startTimeMs 曾滑出官方链接
  30 天窗口导致 3 个用例假失败）——时间相关样例一律用 `utcnow() - timedelta(days=1)` 相对时间。

## 定制监控系统（2026-09-05 上线）

- **模型（迁移 0027）**：custom_feeds（user_id/name/kind=uid|keyword/platform/target_value/
  min_views/push_enabled/is_public/enabled/status=active|disabled(管理端)/interval_minutes/
  next_run_at）+ custom_feed_items（feed_id/external_id 唯一/title/url/cover/view/published_at）。
  **与主内容库完全隔离**（不进 raw_contents/事件引擎/分区）；status 是管理端停用，enabled 是
  用户开关，两者独立。
- **采集**：services/custom_feeds.sweep_custom_feeds(session, redis)——查到期源（next_run_at）、
  每轮限量 20、复用 BilibiliCollector.user_dynamics/search_videos + parse_dynamic/parse_video，
  平台礼貌间隔与主采集共享（platform_slot 注入 redis）；关键词监控按 min_views 过滤。
  调度：scheduler 两循环（本地/生产）每 5 分钟桶入队 collect_custom_feeds（redis key 去重）；
  新消息推送 = kind=custom 投递行 + send_admin_email 内联 SMTP，**每监控每天至多一封**（dedupe）。
- **API**：公开 GET /api/custom/feeds（is_public+enabled+active 目录，带 owner/item_count）与
  /feeds/{id}（私有仅所有者可见，可选登录 _optional_user）；账号 /api/account/custom-feeds CRUD
  （每人上限 10，kind/platform/UID 数字校验；**返回裸数组**，与 keywords 端点一致）；管理端
  /api/admin/custom-feeds（搜索/筛选/分页、PATCH status|is_public|enabled、DELETE、POST {id}/collect
  手动触发）。
- **前端**：顶栏频道组最后是「定制化」→ /custom 公开目录（卡片=名称/类型徽章/目标/条数/所有者）
  + /custom/{id} 监控详情流；账号中心"定制监控"区块（创建表单 kind/target/name/min_views/推送/
  公开 + 行内推送开关/删除/公开徽章）；管理端 /admin/customs（巡查表 + 停用/公开切换/手动抓取/删除）。

## AstrBot 机器人通道（2026-09-11 上线）

- **模型**：bot_targets（管理端配置的会话，config JSON：community/keywords/daily*/updates*/quiet_start·end/
  tools/ai_prompt/ai_provider_id/delivery_mode/chat_controls/reminders_enabled/activities_enabled/manager_ids）+
  bot_deliveries（outbox：dedupe_key 唯一幂等，status pending→sending→accepted/failed/unknown，attempts<3，
  receipt 发送凭据）+ bot_reminders（owner_id=真实发送者，request_key=sha256(target:actor:message_id) 幂等，
  pending→queued）。服务端 `services/bot.py`（query_target 查询工具/run_bot_notifications 巡检/直推）+
  `api/bot.py`（管理 CRUD+/api/bot 查询）+ `api/bot_companion.py`（订阅/提醒/上下文/outbox）。
- **两种投递模式**：direct=服务端经 bot_bridge（site_settings：endpoint+api_key）直推 AstrBot 网关；
  astrbot=插件每 15s 轮询 `/api/bot/outbox/claim` 领取（领取即 attempts+1 并发 receipt），用会话人格 +LLM
  渲染开场白后经 AstrBot 自身连接发送，回执 POST `/outbox/{id}/complete`（receipt 比对，409=凭据失效需人工）。
  **发送中断绝不自动重发**：sending 超 5 分钟标 unknown；failed 5 分钟冷却；attempts<3。
- **会话可辨认（2026-09-12，插件 v1.2.0）**：`/api/bot/targets` 返回**全部会话含已暂停**（携带 name/enabled，
  插件按 enabled 自行门禁，`session_target()` 统一判断）；插件 `/情报会话` 回显注册状态（已注册为「名称」
  （已启用/已暂停）或未注册提示）；插件在全量消息钩子里追踪各 umo 最近活跃，30s 刷新时 POST
  `/api/bot/activity` 回传（服务端存 site_settings `bot_activity`，仅保留已知会话、旧时间不覆盖新时间）；
  管理端会话卡显示 平台·群聊/私聊·尾号8位（title 悬停完整 UMO）+ 最近活跃。**版本号单一来源 main.py
  顶部 VERSION 常量**（register 与 claim payload 共用；曾因 claim 硬编码漏 bump）。
- **去重键**：`daily:{target_id}:{北京时间日期}`、`updates:{target_id}:{sha256(event:{id}:{conclusion}|content:{id})}`
  （同一公告跨平台补证据不重复推送，履约结论变化才再推）、`reminder:{id}`、`manual:{hex}`。
- **插件** `integrations/astrbot_plugin_anisignal`（v1.2.0，部署在 astrbot 容器 /AstrBot/data/plugins/，
  **改插件代码需手动同步进容器**：scp 到 /tmp → docker cp 三个文件 → docker restart astrbot_nnjs-astrbot_nNjs-1；
  AstrBot 面板显示的版本号来自 metadata.yaml，心跳版本来自 main.py VERSION——两处都要对齐）。
  每 30s 拉 `/api/bot/targets` 恢复 qq_official 群场景路由并刷新配置；命令 /情报会话 /情报订阅 /我的提醒 /
  早报 /资讯 /商品 /聊点什么 /情报挑战；llm_tool anisignal_query/subscription/reminder 按会话
  tools/reminders_enabled/chat_controls 门禁；on_llm_request 注入北京时间与最近通知上下文（防把历史通知
  当新消息）。umo/actor_id 只取自真实消息事件。
- **投递带图（2026-09-12，迁移 0032）**：bot_deliveries.media JSON 列存封面 URL；daily 取头条封面、
  updates 取对应条目封面（manual 试发不带图），claim payload 带 `images`，插件用
  `MessageChain().message(text).url_image(url)` 发送（qq_official 走富媒体上传 msg_type=7，已实测通过）。
  人格渲染输出卫生已强化：禁止向群友自述工具能力（"我有搜索和抓取网页的能力"类话术）、禁止照抄搜索摘要
  与 &&like 类乱码、禁止无下文话头，回复限 60 字内（AstrBot web_search 保留开启）。QQ 官方 API 不支持
  消息编辑 → 流式输出不可行，靠短回复缓解。
- **调度与管理**：scheduler bot-sweep 周期跑 send_bot_notifications（提醒到期→queued+投递、每日早报、
  即时更新按 quiet 时段、pending/failed 补投递）；管理端 /admin/bot（会话 CRUD/预览/手动发送/投递记录/
  重试，companion 状态：last_seen<2min 视为在线）。
- **部署**：compose.astrbot.yaml 把 app/collector 加入外部网络 ap_notify_net（COMPOSE_FILE=compose.yaml:compose.astrbot.yaml）。
- **NapCat/OneBot 适配（2026-09-12 审计）**：插件与服务端**完全 UMO 泛化**，接入 aiocqhttp 平台零代码改动
  ——aiocqhttp 的 UMO 是 `平台id:GroupMessage:群号`（纯数字群号），server 的 UMO 正则与管理面板直接兼容；
  插件里唯一的平台特判是 qq_official 的 remember_session_scene 群场景恢复（对其他平台无害）。
  接入步骤：AstrBot 加 aiocqhttp 平台（NapCat OneBot v11 ws 反向/正向）→ 群里发 /情报会话 拿 UMO
  （这次是纯群号）→ 面板照常建会话。NapCat 收全部群消息（无 @ 限制），唤醒词/关键词应答/内容感知插话
  才可行；QQ 官方平台（qq_official）群场景只收 @消息，群主动消息有平台配额。
- **人格（2026-09-12）**：AstrBot 默认人格已从内置 default 改为自建 `qunyou`（群友「阿汐」人设：口语化短句、
  不用 AI 自我描述、被问是不是机器人用玩笑岔开但不谎称真人）。安装方式：直接写 astrbot 容器
  data_v4.db 的 personas 表 + cmd_config.json 的 provider_settings.default_personality（文件带 BOM，
  用 utf-8-sig 读写），**必须 docker kill（SIGKILL）再 start**——优雅重启可能把内存旧配置写回覆盖修改。
  播报开场白 = 该人格 + 插件渲染指令；agnes 限流时开场白降级为原始资料。
- **运维实况**：agnes 免费模型在 08:00 北京高峰常 429——人格渲染失败自动降级"原始资料+原因注记"发送
  （上线首 48h 仅 4/18 条，消息零丢失）；重建 app 容器会让插件报瞬时 ClientConnectorError，15s 周期自愈；
  当前平台组合（qq_official + weixin_oc）**拿不到群名称/群资料**（平台 API 限制），会话辨认靠管理员命名
  + 尾号 + 活跃度，自动拉群名只有接入 aiocqhttp（NapCat/OneBot）后才可行。
  2026-09-12 起首选模型 agnes-3.0-flash（空载延迟 ~7s），插件渲染超时已从 45s 放宽到 100s，人格渲染恢复稳定。

## 未完成 / 下一步选项

- 跨语言公告 AI 语义合并；用户内容 LLM 相关性判定（替代关键词匹配）。
- HTTPS/域名（nginx 80 端口被其他服务占用，需所有者确认划分）。
- 支付/订阅（远期，设计文档阶段 C）。
