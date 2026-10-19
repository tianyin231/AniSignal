# 炼 v3：覆盖率审计 + 查漏补缺（2026-10-19）

## 背景
用户要求"尽量多的都炼进去"。v1 抓了 18573 篇（wiki 共 18574，1 篇 Flow 页放弃），
但需要系统性审计：到底漏了什么、漏的内容值不值得补。

## 做什么（分支 feat/wiki-kb-azurlane，等 v2 跑完再开工）
1. **命名空间审计**：列出抓取覆盖的 MediaWiki 命名空间，确认主命名空间（0）
   无遗漏；统计各命名空间篇数，与 wiki 的 statistics 对照。
2. **失败清单复盘**：读 `data/raw/manifest.json` 的 failed_titles，逐个看能不能救
   （Flow 页、特殊页面、API 限流漏的）。
3. **页面内覆盖率**：抽查几类典型页面，确认解析没丢东西——
   - tabber/折叠标签里的内容（很多攻略把配装藏在 tab 里）
   - 语音台词（voice lines）
   - 剧情对话页
   - infobox 没覆盖到的模板类型（README 里列了 9 种，看有没有第 10 种）
4. **图片 URL 入库（不下载）**：解析时把图片 URL 记进 chunk payload（`images: [url...]`），
   bot 回答时可直接甩 wiki 原链配图。零存储成本，纯赚。
5. **other 16287 拆解**：README 说 other 有 16287 篇，拆一下都是什么类型，
   确认没有整类内容被当成噪音洗掉。

## 输出
- 覆盖率审计报告写进 README（漏了什么、补了什么、为什么不补）。
- 补上的内容重新跑 parse → chunk → embed（增量 upsert，别重建索引）。
- 重跑 eval 确认没退化。
- commit（中文一句话 message）+ 一句话报告。
