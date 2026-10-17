# 竞品灵感移植规格书（统筹：deepseek，执行：step-5-preview）

> 来源：`~/workspace/research_notes/anisignal-competitor-inspiration-20261017-1856/report.md`
> 分支：`feat/competitor-inspiration`。许可证红线：RSSHub/Follow 是 AGPL，只借鉴设计思路不复制代码；
> changedetection.io（Apache 2.0）/Huginn（MIT）可参考实现但仍以自写为主，保留致谢注释。

## 批次 1（进行中）：AI 降噪意图规则
抄 changedetection.io `llm/`（evaluator/prompt_builder）+ Visualping 重要性标记。
- 监控任务配自然语言「意图描述」；命中经 AI 打标：重要 / 一般 / 忽略
- 重要→推送，一般→仅入库，忽略→丢弃

## 批次 2：RSS + Webhook 万物输出（低难度）
抄 Inoreader「过滤结果再输出 RSS」+ Google Alerts + camelcamelcamel。
- 每个监控任务、每个事件自动生成带 token 的 RSS 2.0 feed（只读、token 鉴权、可撤销）
- 通知渠道新增通用 Webhook：POST JSON（n8n 兼容格式：title/link/content/source/timestamp）
- 管理端可查看/复制 feed 地址、webhook 投递记录

## 批次 3a：规则引擎 + 声量异常告警
抄 Inoreader Rules + Brand24 Anomaly Detector + Mention 布尔语法。
- Rules：`if 命中满足条件 then 执行动作`；条件=关键词（含 AND/OR/NOT 布尔组合）/来源/重要性档（依赖批次1）；
  动作=打标签 / 转为事件 / 选择通知渠道 / 标记已读
- 声量异常：按监控任务统计单位时间命中数基线（滑动均值+标准差），超阈值触发「热度预警」通知
- 信源分级：B站 UP主/官号按粉丝量分 tier，通知中标注来源权重（高权重来源命中优先推送）

## 批次 3b：商品档案页
抄 MyFigureCollection + 什么值得买 + Keepa + 拆盒网字段范式。
- 一物一档：档案头（名称/封面/官方链接）+ 官方公告时间线 + 价格/热度曲线 + AI 差评聚合 + 发售日历
- 状态标记：已预订 / 已到货（用户手动）
- 到价提醒：设心理价位，采集到低于该价触发通知（抄 SMZDM 降价提醒 + Distill 条件告警）

## 批次 3c：每日情报简报
抄 Readwise Reader + Brand24 每日洞察 + 识微商情。
- 定时任务每日生成：昨日事件汇总 / 重要命中 Top / 商品进展 / 声量回顾，AI 写成简报
- 经邮件 / QQ（AstrBot/NapCat）推送，管理端可预览历史简报
