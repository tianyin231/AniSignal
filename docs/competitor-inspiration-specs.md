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

## 批次 3b：商品档案页
抄 MyFigureCollection + 什么值得买 + Keepa + 拆盒网字段范式。
- 一物一档：档案头（名称/封面/官方链接）+ 官方公告时间线 + 价格/热度曲线 + AI 差评聚合 + 发售日历
- 状态标记：已预订 / 已到货（用户手动）
- 到价提醒：设心理价位，采集到低于该价触发通知（抄 SMZDM 降价提醒 + Distill 条件告警）

## 批次 3c：每日情报简报
抄 Readwise Reader + Brand24 每日洞察 + 识微商情。
- 定时任务每日生成：昨日事件汇总 / 重要命中 Top / 商品进展 / 声量回顾，AI 写成简报
- 经邮件 / QQ（AstrBot/NapCat）推送，管理端可预览历史简报
