# 碧蓝航线知识库"炼"任务 brief（2026-10-18）

目标：把碧蓝航线 BWIKI 炼成 bot 可用的知识库。工作分支 `feat/wiki-kb-azurlane`（已建好，直接在上面干）。

## 数据源（已验证可用）
- MediaWiki API：https://wiki.biligame.com/blhx/api.php，MediaWiki 1.37
- 规模：16025 篇条目。API 支持批量取（一次 50 页），礼貌频率 1-2 req/s，带重试。

## 产物（`kb/` 目录）
1. `fetch.py`：分页拉全量条目 wikitext → `data/raw/`（gzip）。只拉文本，**不下载图片**。
2. `parse.py`：mwparserfromhell 解析。
   - 结构化路：infobox（舰船/装备模板）抽成 SQLite 表 `data/kb.db`（表结构自己设计：ships/equipments/skills 等）。
   - 文本路：正文清洗（去导航栏、编辑标记、分类、"最后编辑于"噪音）→ 按语义切 chunk（一段讲一件事，别拦腰切）→ `data/chunks.jsonl`（每行：text, source_title, source_url）。
3. `embed_index.py`：本地 embedding（sentence-transformers `bge-small-zh`，CPU 跑）→ 向量库。优先 Qdrant 单二进制；环境受限就用 hnswlib/sqlite-vec，但**检索接口要抽象一层**，方便以后换。
4. `eval.py`：30-50 个测试问题。查表类（"光辉二技能叫什么"）精确匹配判分；问答类抽查。输出通过率报告。
5. `kb/README.md`：pipeline 说明、数据规模、评测结果、怎么跑。
6. `.gitignore`：`data/` 不进 git（只提交代码+文档，数据留云电脑）。

## 质量门（必须遵守）
1. **先拉 200 篇做 spike**：infobox 结构化抽取成功率 <60% 就停下报告，不要继续全量。
2. spike 通过 → 全量 16025 篇。
3. 全量后跑 eval，报告通过率。

## 红线
- 尊重 API 频率，别被封。
- commit 用中文一句话 message。
- 完工后报告：数据规模（篇数/chunk 数/向量数/磁盘占用）、结构化抽取率、eval 通过率、commit hash。
