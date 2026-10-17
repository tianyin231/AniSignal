# 竞品灵感移植规格书（统筹：deepseek，执行：step-5-preview）

> 来源：`~/workspace/research_notes/anisignal-competitor-inspiration-20261017-1856/report.md`
> 分支：`feat/competitor-inspiration`。许可证红线：RSSHub/Follow 是 AGPL，只借鉴设计思路不复制代码；
> changedetection.io（Apache 2.0）/Huginn（MIT）可参考实现但仍以自写为主，保留致谢注释。

## 批次 1（进行中）：AI 降噪意图规则
抄 changedetection.io `llm/`（evaluator/prompt_builder）+ Visualping 重要性标记。
- 监控任务配自然语言「意图描述」；命中经 AI 打标：重要 / 一般 / 忽略
- 重要→推送，一般→仅入库，忽略→丢弃

## 批次 2（已完成）：RSS + Webhook 万物输出（低难度）
抄 Inoreader「过滤结果再输出 RSS」+ Google Alerts + camelcamelcamel。
- 每个监控任务、每个事件自动生成带 token 的 RSS 2.0 feed（只读、token 鉴权、可撤销）
- 通知渠道新增通用 Webhook：POST JSON（n8n 兼容格式：title/link/content/source/timestamp）
- 管理端可查看/复制 feed 地址、webhook 投递记录
- 落地：feed token 按 kind+ref_id 唯一、`token_urlsafe(32)` 生成、`compare_digest` 校验，撤销/重建后旧链接 404；
  webhook 每用户最多 5 个、超时 10 秒、失败重试 3 次、按 `webhook:{id}:content:{id}` 去重；
  调度每 5 分钟扫描一次命中（未配置 webhook 时零开销），管理端与账号中心均可查看投递记录

## 批次 3a（已完成）：规则引擎 + 声量异常告警
抄 Inoreader Rules + Brand24 Anomaly Detector + Mention 布尔语法。
- Rules：`if 命中满足条件 then 执行动作`；条件=关键词（含 AND/OR/NOT 布尔组合）/来源/重要性档（依赖批次1）；
  动作=打标签 / 转为事件 / 选择通知渠道 / 标记已读
- 声量异常：按监控任务统计单位时间命中数基线（滑动均值+标准差），超阈值触发「热度预警」通知
- 信源分级：B站 UP主/官号按粉丝量分 tier，通知中标注来源权重（高权重来源命中优先推送）

落地：
- 布尔式自写递归下降解析器（`services/boolean_query.py`），支持 `AND/OR/NOT`、`&&/||/!`、逗号（等同 OR）、
  括号（半角与全角）与全角 `＆｜！`，容错处理悬挂运算符、未闭合括号和空表达式（`(A`、`A NOT` 均能给出可用结果）；
  命中文本池=标题+正文+来源名+URL+命中关键词
- 规则执行点在「命中入库后、通知分发前」（`services/rules.py: apply_rules`），按 `priority` 升序执行；
  在 collect 主链路里挂在意图打标写回之后（`services/collect.py`），否则带「重要性档」条件的规则看不到本轮新 tier；
  单条规则失败只跳过它自己并记 `RuleHit.error`，下一轮自动重试，绝不阻塞采集与其它规则（fail-open 且可恢复）；
  已成功的（规则, 内容）组合记账后不重复执行
- 动作：打标签（`RawContent.rule_tags`，上限 8 个）、转为事件（走 `events.run_aggregation`，ensure 语义幂等）、
  标记已读（新增 `rule_acked` 字段，只服务规则自身）、通知（复用批次 2 的 webhook / 邮件 + 批次 1 的 QQ 直推，
  去重键分别 `rule:{rid}:content:{cid}:{渠道}:{目标}`）
- 声量异常：`services/volume.py` 按（平台, 命中关键词）即监控任务分组，按窗口大小聚合命中数，维护滑动基线
  （均值+总体标准差），`count > mean × multiplier` 且 `count >= min_hits` 且样本数 `>= min_samples` 才告警；
  告警按 `volume:{group}:{stamp}` 去重、等级 critical，管理端可手动 sweep
- 信源分级：`services/source_tier.py` 按粉丝量阈值（默认 10w / 100w 两档，阈值可配）分 `high/mid/low/unknown`，
  规则执行前按当前阈值重算本批来源档位，通知标题带 `[高权重]` 一类的权重标注
- 迁移 `0036_rules_volume_source_tier`：新增 `rules` / `rule_hits` / `volume_baselines` 三表，
  `raw_contents` 加 `rule_tags/rule_acked/rule_acked_at`，`sources` 加 `tier`（含索引）；已在临时 sqlite 库上验证
  `upgrade` 与 `downgrade -1` 双向干净
- 管理端 API `/api/admin/rules-engine`：规则 CRUD + 条件试跑预览 + 命中台账 + 声量基线查看/扫描 + 阈值配置；
  静态路径（`config`/`volume`/`hits`/`preview`）先于 `/{rule_id}` 注册，否则会被路径参数吞掉报 422
- 前端 `web/app/pages/admin/rule-engine.vue`：规则列表/表单（含动作编排与布尔式预览）、命中台账、声量基线、
  阈值与信源分级四个页签；admin 布局加入口
- 测试 `server/tests/test_rules_engine.py` 47 条全绿：布尔解析（13 组参数化 + 容错 + 描述）、规则动作执行、
  异常告警触发/不触发边界（min_hits、min_samples、multiplier、cooldown）、信源分级、配置规范化、管理端接口

## 批次 3b（已完成）：商品档案页
抄 MyFigureCollection + 什么值得买 + Keepa + 拆盒网字段范式。
- 一物一档：档案头（名称/封面/官方链接）+ 官方公告时间线 + 价格/热度曲线 + AI 差评聚合 + 发售日历
- 状态标记：已预订 / 已到货（用户手动）
- 到价提醒：设心理价位，采集到低于该价触发通知（抄 SMZDM 降价提醒 + Distill 条件告警）

落地：
- 迁移 `0037_product_dossier_briefs`：新增 `product_profiles` / `product_price_points` / `product_feedbacks` /
  `product_track_states` / `product_price_alerts` / `daily_briefs` 六表；`product_profiles` 与 `products` 一对一
  （唯一外键 + CASCADE），价格点带 `(product_id, captured_at)` 复合索引，反馈按 `(product_id, content_id)` 唯一
  （同一内容重复聚合只更新结论）；已在临时 sqlite 库上验证 `upgrade` 与 `downgrade -1` 双向干净
- 档案头只增不改：`products` 的履约字段仍由 fulfillment 引擎负责，档案（封面/官方链接/别名/发售时间）单独一张表；
  管理端 `PUT /api/admin/product-dossiers/{id}` 只更新显式给出的字段（空 payload 报 400）
- 价格抽取是「保守策略」旁路观察（`services/product_dossier.py: extract_price`）：只认 `¥199` / `199元` /
  `RMB 199` / `定价：199` 一类带货币符号、单位或明确前缀的数字，排除版本号、期数、阶段号上下文；
  数字被更长数字截断时（"编号 123456 元"、"¥1234567"）判为非价格——到价提醒误报比漏报更烦人
- 曲线按天聚合（同一天多个采样取最后一次观测，hits 取当日最大），`sweep_price_points` 每个商品单轮最多记 4 个点，
  只记「比当前价更低」或「当前还没有价」的观测，避免同一条内容反复刷曲线；热度分 = 当日命中 + 近 7 日日均命中
- AI 差评聚合复用既有 AI 通道（`ai.chat` + `ai.record_usage`），未配置 AI 或单条调用失败都回退原文片段，
  本轮连续失败即停打省额度；结果可人工确认（`PATCH /api/admin/product-dossiers/feedback/{id}`）
- 到价提醒按四条既有渠道通知，各自 try/except 互不影响：QQ 机器人（critical 穿透静默时段）、邮件
  （`NotificationDelivery` 落库）、OnePush、Webhook；去重键 `price-alert:{alert_id}:{第几次}`，
  重新设价视为新一轮提醒（计数复零，否则会和上一轮撞号）
- 调度：`sweep_price_alerts` 走 arq `sweep_price_alerts` 任务，生产模式按 15 分钟桶去重（`price-sweep:{bucket}`），
  本地模式直接跑；管理端可手动 sweep 与查台账
- 接口分工：`/api/products/{slug}/dossier|series|feedback` 只读公开（`/api/release-calendar` 单独占路径，
  因为 `/api/products/{slug}` 会吞掉同前缀静态路径）；`/api/account/product-tracker` 管用户自己的状态与心理价位
- 测试 `server/tests/test_product_dossier_briefs.py`（3b 部分 33 条）全绿：价格抽取 13 组参数化、曲线窗口与按天聚合、
  热度分计算（隐藏/跨社区不计）、采样扫描升降价边界、AI 聚合三态（未配置/正常/失败回退）与幂等、
  状态与提醒 CRUD、到价触发与去重、日历分组（含状态推断与预售窗口回退）、档案聚合、管理端与账号中心接口含鉴权边界

## 批次 3c（已完成）：每日情报简报
抄 Readwise Reader + Brand24 每日洞察 + 识微商情。
- 定时任务每日生成：昨日事件汇总 / 重要命中 Top / 商品进展 / 声量回顾，AI 写成简报
- 经邮件 / QQ（AstrBot/NapCat）推送，管理端可预览历史简报

落地：
- 先结构化采集再交给 AI：`services/briefing.py: collect_sections` 四个分节都是确定性查询（可重复执行得到同样结果），
  AI 只负责把结构化数据写成一段 200 字导读；AI 不可用/失败/编造数字时一律退回模板文——简报永远能生成
- 分节口径：昨日事件=当日新增且 `is_active` 的归并事件（按重要度排序，带证据条数）；重要命中 Top=意图打标
  `important` 的采集内容（依赖批次 1）；商品进展=昨日有新证据的商品（带当前价观测与履约阶段）；
  声量回顾=命中总数/平台分布/关键词 Top，已标记 `ignore` 的不计入（与机器人推送同一口径）
- 防幻觉：`_summary_supported` 校验 AI 摘要里出现的每个数字都必须能在结构化事实里找到，找不到就整段退回模板文
- 口径是「北京时间昨天」：`_brief_day` 用 `Asia/Shanghai(+8)` 换算，早 8 点后生成时覆盖的是前一日
- 推送复用既有渠道：邮件给「启用通知 + 已验证 + active」的用户（`NotificationDelivery` 落库，去重键
  `brief:{id}:{user_id}`），QQ 推给每个启用的机器人目标（critical 穿透静默时段，去重键 `brief:{id}`）；
  单渠道失败不影响另一渠道，结果写回 `brief.delivered`
- 调度：arq `generate_daily_brief` 任务，生产模式按天去重（`brief:{day}`，Redis key `anisignal:brief-enqueued:{day}`），
  本地模式北京时间 08:00 后跑一次；同一天重复跑不重复生成也不重复发
- 管理端 `/api/admin/briefs`：历史列表/详情（含渲染好的邮件标题与正文，预览与真实邮件共用 `render_brief_text`）、
  `preview` 试跑不落库不推送、`generate` 手动补某一天、`send` 手动重推
- 测试（3c 部分 34 条）全绿：四节采集（隐藏/忽略过滤、空数据安全）、AI 摘要三态与数字校验、
  生成幂等与 force、渲染分节完整性、邮件与 QQ 推送及去重、无渠道时标记 failed、调度入口（含时区口径）、
  管理端列表/详情/预览/补生成/重推与未授权边界
