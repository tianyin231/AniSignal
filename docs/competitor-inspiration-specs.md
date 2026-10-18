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

## 批次 4·REA：Distill 条件告警引擎设计笔记（2026-10-18）

> 纯静态逆向分析对象：Distill Web Monitor Firefox 扩展 v3.6.12（已解包、JS 未压缩）。
> 闭源软件的功能设计研究，已获授权；以下只记机制设计，不复制源码、不写实现代码。
> 术语对照：Distill 把「监控任务」叫 **sieve（筛子）**，把「条件」叫 **rule（规则）**，把一次运行叫 **work**。

### 0. 总体形状
- 本地优先的 MV2 扩展，云端只做同步与代发，**检查永不放在云端**（见第 3 节）。
- 条件不是独立实体：sieve 上用 `rule_id` 指向 `rules` 表（`{id, name, config, version}`），
  另有一个挂在用户 prefs 上的 **global rule** 对账号下所有 sieve 生效。
- 数据表分工：`sieves`（任务）/ `sieve_data`（每次变化一条历史）/ `actions`（动作）/
  `rules`（条件）/ `works`（每次运行日志，含 err）/ `sieve_snapshots`（出错时的页面快照）。

### 1. Condition 数据模型
- **两代并存，用 `version` 字段（`'1.0.0'` / `'2.0.0'`）在运行时选 evaluator**，不做数据迁移、旧配置继续能跑。
- **V1（classic）**：递归树，节点两类——单规则 `{type, contentType, rule:{type, params:{input}}}`
  与规则组 `{type, op: AND|OR, rules:[...]}`；**组里可以再嵌组，任意深度**。组为空或缺省 → 恒真
  （空条件 = 总是触发，这是贯穿两代的同一个约定）。
  - 单规则操作符共 10 个：非空 / 包含文本 / 不含文本 / 有数字小于 / 有数字大于 /
    数字下降超过 / 数字上升超过 / 数字下降百分比超过 / 数字上升百分比超过 / 匹配正则。
  - 每个单规则自带一条 **contentType 轴**：当前全文 / 只看新增片段（配对看删除片段）/ 上一版全文。
    即「拿哪一段文本去比」是条件自身的一等属性，不是全局开关；新建规则默认落在「新增片段」上。
  - **数字比较是位置配对的**：从新旧文本各抽出一个数字序列，按下标配对，任一位置满足即命中
    （既不是比总和，也不是只比第一个数）。抽取器还认三种地区数字格式（`1,.` / `2.,` / `3 ,`），
    格式写在规则配置的 `numberFormat` 里，跟着规则走而不是跟着用户 locale 走。
  - 百分比类未做除零保护（旧值为 0 会算出 Infinity）——设计上的粗粒点，不值得抄。
  - 正则参数是 `{expr, flags}`，默认 flags `gim`；V1 evaluator 直接返回 `match()` 的真值/数组，
    到 V2 才规整成严格布尔。
- **V2（modern）**：**扁平 OR-of-ANDs（合取范式）**，用前缀（波兰）表达式数组序列化——
  顶层 `["or", 组1, 组2, …]`，每组 `["and", 三元组, 三元组, …]`，三元组是 `[op, left, right]`。
  - `rules.length < 2`（顶层只有 `["or"]`、没有任何组）→ 恒真。
  - evaluator 只认这一种扁平形状：跳过两个头部槽位后 `some(组 => every(三元组 => 求值))`。
    编辑器侧的解析器理论上是通用前缀树，但 UI 只产出「Or 根 + 若干 And 组」，
    且**组内最后一条被删时整组一起删**——用 UI 不变量兜住 evaluator 的假设，避免出现空组
    （空组会让 `every` 恒真、永远触发）。这是很实用的一招：把易错的不变式放在唯一的生产者手里。
  - 左操作数是四个具名变量：`$new`（当前全文）/ `$old`（上一版全文）/ `$diff.new`（新增片段）/
    `$diff.old`（删除片段）。
  - 右操作数按操作符的 `fieldType` 分型：text / number / regex。
  - 操作符 18 个，**全部成对给出正与反**：contains / not_contains、starts_with / not_starts_with、
    ends_with / not_ends_with、is_empty / not_is_empty、length_lt / length_gt、
    has_num_lt / has_num_gt、has_num_decr_min / has_num_incr_min、
    has_num_decr_pct_min / has_num_incr_pct_min、match_regex / not_match_regex。
  - **四个 delta 类操作符强制要求左操作数是 `$new`，否则直接抛错**——「需要历史状态」这件事被编码进
    操作符签名里，而不是靠文档约定。值得抄。
  - 文本类 contains/starts/ends 一律大小写不敏感；is_empty 用 isEmpty 语义（空串/空数组/空对象都算空）。
- **global rule 与 per-sieve rule 是 AND 关系**：global 不过，整个 sieve 直接不触发；global 未配置视为恒真。
  两者各自带 version 与 numberFormat，互不复用。global 相当于「账户级兜底过滤」。
- **命中结果回写到历史数据上**：`sieve_data.meta` 序列化「本次用了哪版 global / 哪版 sieve 规则及配置」，
  外加一个 `triggered` 布尔。历史因此自带「当时为什么触发 / 为什么没触发」的可解释性——
  排查误报时不用去猜当时的规则版本。

### 2. 状态变化触发语义
- **判定单位是「整段抽取文本」的相等性，不是「diff 是否非空」**。取该 sieve 最近一条历史与新结果按
  `config.dataAttr`（默认 `text`，可切 `data` 拿整页 HTML）比较；默认模式去掉所有空白再比，
  另一种模式按 `\s+|\b` 分词后逐词比。
- **相等 → 什么都不做**（连历史都不落），只顺手清掉上一次的 err。不等 → 落历史、算 diff、进动作编排。
- **首次采集不算变化**：没有上一条历史时，给 sieve 打一个「已读时间 = 数据时间 + 1ms」，
  让首帧直接被判为已读、不产生未读红点。一个很小的技巧，省掉一整套「首帧特殊分支」。
- diff 在独立 Web Worker 里算，产出 `[[op, text], …]`；**「added / removed」就是 diff 的两个投影**
  （分别收集 INSERT / DELETE 片段后 join），没有独立的状态机；「changed」则是整段不相等这个事实本身。
- 因此整体是 **「状态变了才推」而不是「每次命中都推」**：没有变化就没有 diff、就没有动作；
  条件（rule）是在「已经变了」之后再加一道过滤，**不是触发源**。
- **条件不命中时，把 sieve 的已读时间置为当前时间**——这次变化被静默吞掉，不进收件箱、不留未读。
- 去重分三层，注意别混：
  1. 内容层：整段文本相等 → 无事件；
  2. 动作层：global 动作 + sieve 动作合并后按 `type + config` 去重，同类只跑一次；
  3. 错误层：`err.count` 是**连续失败计数**（读最近一条 work log 的 count + 1，没有就置 1），
     错误通知按这个计数的等号触发（见第 5 节）。
- **没有冷却期 / 静默期概念**。节奏完全由「检查间隔 + 内容相等性」决定：内容不变就天然静默。
  唯一的节奏控制是全局时间槽（第 3 节）和错误通知的固定 15 分钟。
- Live（实时）模式另有一套：调度返回 0 → 立刻跑，之后由内容脚本里的 MutationObserver 盯着 DOM
  （attributes 白名单 + childList + characterData + subtree）；**observer 在每次 check 前先 disconnect、
  check 完再 observe**（天然防重入），且内容脚本侧还做一次 `lastResult.text != text` 的比对，
  只有文本真的变了才上传。实时模式没有空文本重试，只有超时兜底。
- 历史留存：`sieve_data` 与 `works` 都只保留最近 10 条——**「取最近 10 条」既是调度判据也是容量上限，
  两者共用同一个数字**，存储开销因此有硬上界。

### 3. 检查调度模型
- 三类调度器，同一接口 `getSchedule(params, logs) -> 距下次运行的秒数`，返回 -1 表示不排：
  - `LIVE`：返回 0，立即跑 + 常驻 MutationObserver；
  - `INTERVAL`：`max(now, lastCheckedOn + interval) + 1`（`+1` 抵消秒级取整）；
    interval 未配置或 ≥ 30 天（`TIME_INFINITE`）→ -1，等于暂停；
  - `RANDOM`：在 `[min, max]` 间随机取偏移，其余同 INTERVAL。
- **失败重试 = 固定 2 分钟快速复查，且带「最近 10 次里有成功过」的闸门**：
  - 判据是最近一次运行是否带 err；
  - 有错 → 在最近 10 条 work log 里找有没有 err 为 null 的；有 → 返回 `lastCheckedOn + min(120, interval)`；
    没有 → 仍按正常 interval 排。
  - 即**连续失败且近期毫无成功记录的，不走快速重试**，退回正常节奏。既省资源，也避免目标长期挂死时
    每 2 分钟撞一次把错误通知刷爆。快速重试上限 120 秒，间隔比 120 秒短时取间隔。
- 队列与并发：一个队列 + 每秒 tick 一次，`nworkers`（默认 3）限并发；手动「立即检查」是先取消原定时再入队。
- 单次运行：按 `content_type` 分派（HTML / FEED / JSON / XML）。HTML 路径是开一个 pinned 的非激活标签页
  加载页面（可先放一段 macro 预演登录墙）→ 等 `delay` → 逐 frame 调过滤接口 → 拼 html/text →
  可选 `config.regexp` 再筛一遍文本 → 回调。
  - **空文本重试**：抽出来是空串时隔 5 秒重试，最多 4 次；若要的是整页 HTML 则最多 1 次。
    超次数仍为空 → 仅在「未显式允许空结果」时记为 SELECTION_EMPTY 错误并附页面快照，否则放行空结果。
  - 超时默认 60 秒，超时后把回调替换成 no-op 防止二次回调。
- **本地 vs 云端分工（最值得抄的一条）**：
  - 云端**从不触发检查**。广播通道（SSE）传过来的消息体只有 `{name, id, op, ts_mod}`——
    **纯缓存失效信号，不带内容**。
  - 客户端收到后只做一件事：拿本地该行的 `ts_mod` 与信号里的比，差值超过 5 秒才判本地过期，
    然后才去拉同步；`sieve_data` 还额外要求 `op === 'U'` 才拉。只有 SSE `open` 事件才触发一次全量同步。
  - **心跳 2 分钟**收不到广播就重连；连不上就 10 秒后退避重试。
  - 反向（本地 → 云端）是 **2 分钟去抖的批量推送**，写操作先标 `_state`（POST/PUT/DEL/…）再由 SyncMan 统一刷。
  - 云端跑的监控（`crawler_id`）在本地根本进不了调度：准入判据是 `clientIds.includes(sieve.client_id)`。
    **归属权写在数据上，不在代码里**——切归属只改一行数据，不用发版。
  - 多实例协调用 PeerJS：按 client group 选 coordinator，`clientIds = 本组 ∪ 自己`，
    组内只有 coordinator 认领该组的 sieve；对端断连就重选并全量重排。
    本地弹窗/音频动作还会广播给同组其它实例，对端收到后延迟 2 秒再放——多设备不同时响。
- 全局时间槽：按星期几映射多个 `{start, end}` 时段，每分钟算一次并写进 `active`；
  不在槽内就 `uninit()`，等于全局暂停。**「全局静默期」只在这一处实现**，不与 per-sieve 逻辑纠缠。

### 4. 元素选择器组织
- locator 是三字段模型：`{type: 'css' | 'xpath' | 'js', expr, fields?}`，三种引擎走同一张分派表，
  未知 type 直接配置错。js 引擎在页面里 eval 表达式，接受 Node / NodeList / 数组；
  **eval 前先把 `alert/confirm/prompt` 局部遮蔽掉**，防选择器弹窗骚扰用户。
- sieve 侧三层组织：`selections[0].frames[]`，每 frame `{index, includes[], excludes[]}`，
  `index` 是 iframe 序号（0 = 顶层）。多 frame **按 index 降序处理**（最内层优先），
  结果按顺序字符串拼接——frame 之间没有结构化边界，就是文本串联。
- **include/exclude 不是集合差，而是「标记 + 自上而下剪枝」的两趟算法**，跑在文档克隆上（不碰活 DOM）：
  1. 给 include 命中元素打 `include__`，并给它们的**全部祖先**打 `hasinclude__`；
     给 exclude 命中元素打 `exclude__`，属性节点额外打 `xdel-<名>`（事后连原属性一起删，用来清 `on*`）。
  2. 剪枝三趟：a) 删掉「有 exclude__ 且无 hasinclude__ 且无 include__」的；
     b) 对 `[exclude__][hasinclude__]` 逐个剪（跳过 include__ 的、删掉无 hasinclude__ 的、否则只删文本节点）；
     c) 从 documentElement 开始自上而下删「无 include__ 且无 hasinclude__」的元素
     （HTML 元素特殊处理成清空而不是移除，否则 documentElement 变 null）。
  - 语义要点：**exclude 在 include 内部依然生效**（差集语义）；`hasinclude__` 向上传播保证了
    「被选中元素所在的树枝一定保留」。属性节点也参与差集，所以排除能精确到单个属性。
- `fields` 子模型：locator 可带 `fields: [{name, type}]`，type 三种——`builtin`（`name === 'text'` 走内置
  文本抽取）、`property`（读 `el[name]`）、`attribute`（读 `getAttribute`，缺失返回字面量 `"undefined"`）。
  非 builtin 字段的值 push 进文本缓冲，多字段用 `\n` 连接。**这是「从一个元素同时取文本 + 若干属性」的机制**，
  让监控能从 `data-price` 一类 DOM 属性取值，而不只是 innerText。
- 文本归一化是刻意稳定化的：块级元素（或一组 BREAKING_ELS 标签）前插 `\n`，一组行内标签前插空格；
  SCRIPT/STYLE/NOSCRIPT 默认跳过（除非被 fields 选中）；最后 trim + 压缩连续空行 + 折叠制表空格。
  **文本形态越稳定，第 2 节那个「整段文本相等」判定越少被排版噪声抖动骗到。**
- 默认排除集写在代码里而非用户配置：`script, noscript`、`on*` 属性、`style`、`link[rel=stylesheet]`、
  `style` 属性、`frame`、`iframe`；默认包含 `base`（让相对链接可解析）。可由 `includeScript` / `includeStyle` 翻转。
  注释默认删。
- 另有一条独立的**文本后过滤**：`config.regexp = {expr, flags}`（旧格式裸字符串，自动补 `gim`），
  在 selector 结果上再 match 一次，多匹配用空格连接，无匹配则置空（进而触发空文本重试/报错）。
  **与条件里的正则职责不同：这个在采集侧收敛内容，那个在告警侧做判断**——分工很清楚。
- 选择器生成（picker）的可借鉴细节：对 class / 属性值做「可读性打分」（长度 3–15 才候选，
  数字/特殊字符/不规则大写都罚分），低于 cutoff 才采用，否则退化成 tagName；
  XPath 值过长时用 `contains(@attr, 可读片段)` 代替全等；路径不唯一时补 `[n]` 位置索引。

### 5. 通知动作编排
- 动作也是数据表，sieve 级 + 用户级 global 两级，**合并去重后串行执行**，单个失败不阻断其它。
- 分发表 `ActionDescriptors[type] = {descriptors, act}`。**descriptors 是自描述的表单 schema**
  （`{name, type, desc, must}`，type 有 STRING_TEMPLATE / HTML_TEMPLATE / SRC / DURATION），
  「配置长什么样」和「怎么执行」写在同一个对象里，UI 直接由 descriptors 渲染表单——
  很干净的动作插件化方式，新增一种动作只需要加一个表项。
- 云端类（email / sms / push / webhook / slack / discord）**全部走同一个后端 `/agents/actions/<kind>`**，
  扩展只负责把 context（sieve、sieve_data、渲染好的 html、logData）POST 过去，**发送在云端做，
  本地不存任何渠道凭证**；未登录直接 EAUTH。webhook / slack / discord 传结构化 `data`，
  email / push / sms 传 `text`。
- 本地类只有三个：桌面通知（写一条本地消息表 + 系统通知，id 为 `update--<sieve_id>`，
  点击后打开并标记已读）、直接开标签页打开目标 URL、播放音频（src 支持 `tone:` 前缀的间接寻址）。
  弹窗与音频各有一个 pref 开关，**动作仍在，只是被开关短路**，而不是把动作删掉。
- **macro 动作**是唯一形成回路的一环：命中后可以再放一段录制的浏览器宏（采登录墙后面的内容）。
  执行前先查配额，耗尽返回配额错；宏失败不阻断，错误信息回传后端留痕。
  **「条件 → 动作 → 再采集」的回路**在这里闭合（注意只是再落一次数据，不会立刻回到条件判定）。
- **错误通知是独立于规则体系的一条支线**（走 `err.count` 而不是 rule）：
  - `errorAction.minCount` 默认 **5**：某个 monitor 的**连续失败数恰好等于 5** 时才加入未读错误清单
    （清单持久化在 prefs 里）。用等号不用 ≥，保证一个 monitor 只入册一次。
  - **音频一次性锁存**：模块级布尔，只在「错误通知开关开着 + 服务激活 + 连续失败数已达阈值 + 还没响过」
    时放一次音频然后置真；直到用户关掉再打开错误通知开关才复位。**一次错误浪潮只响一声。**
  - **错误弹窗 15 分钟固定节奏**：`errorAction.interval` 默认 15 分钟，一个自调度的 setTimeout 环，
    每圈查所有「启用中且有错」的 sieve，与未读错误清单取交集，聚合成**一条**系统通知
    （标题固定，正文最多列 3 个 monitor 名 + 「and N more…」，单个错误时附 code 与 message）。
    通知 id 固定，反复 create 同一 id 等于覆盖，**不会刷屏**。用户点掉该通知即清空清单。
- 本地弹窗/音频动作还会广播给同组其它浏览器实例（对端延迟 2 秒执行）——本地提醒跨设备 replicate。

### 可借鉴点（按性价比排序）
1. **「变了才推」用整段归一化文本相等判定**，规则只是第二道过滤；归一化（去空白/块级换行）做扎实，
   误报率能直接压下去。anisignal 现在缺这层采集侧收敛。
2. **两代条件格式按 version 并存、运行时选 evaluator**，旧配置不迁移照样跑；V2 用扁平 OR-of-ANDs
   前缀数组，序列化简单、UI 与 evaluator 共享同一个「不变量由生产者保证」的约定。
3. **delta 类操作符把「需要 `$new`」写进签名**（左操作数不是 `$new` 直接抛错），比文档约定可靠。
4. **数字比较按位置配对 + 地区数字格式跟随规则配置**，比「抽第一个数/比总和」准得多。
5. **失败重试带「最近 N 次里有成功过」闸门**，避免长期挂死的目标被反复撞；N 与历史留存上限同一个数。
6. **云端只发缓存失效信号（`{name, id, op, ts_mod}`）不触发检查**，本地用 5 秒时间差判过期；
   反向推送 2 分钟去抖。监控归属写在数据的 `client_id` 上，不在代码里。
7. **include/exclude 的「标记 + 向上传播 + 自上而下剪枝」三趟算法**，差集语义且能精确到单个属性；
   在文档克隆上跑，不碰活 DOM。
8. **动作的 descriptors 自描述表单** + 云端统一 `/agents/actions/<kind>` 代发，本地零凭证。
9. **错误支线独立于规则**：连续失败等号入册、音频一次锁存、错误弹窗固定节奏 + 固定通知 id 覆盖。
10. **命中结果把「当时用的规则版本与配置」回写进历史**，误报可追溯——这个成本极低，收益很高。
