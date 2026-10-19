# 多游戏知识库（kb/）

把游戏 wiki（[碧蓝航线 BWIKI](https://wiki.biligame.com/blhx)、
[明日方舟 PRTS](https://prts.wiki)）炼成 bot 可检索、可查表的知识库。
四个阶段：**抓取 → 解析 → 向量索引 → 评测**，全程本地跑，不调外部 API（模型 embedding 也是本地 CPU）。

## 一游戏一配置

v1–v3 只炼碧蓝航线，wiki 地址、infobox 白名单、chunk 规则、collection 名全写死在代码里。
炼第二款游戏时这些要整套换掉，但 fetch/parse/embed/eval 的代码一行都不该动——
所以全部收进 `kb/games/<name>.yaml`，脚本统一 `--game <name>`（不给就是默认的 azurlane，
行为与 v1–v3 完全一致）：

```bash
cd ~/workspace/anisignal/kb

# 阶段 1：抓取（断点续拉，随时可 Ctrl-C，重跑同一命令继续）
./.venv/bin/python fetch.py --game arknights --spike 200   # 先拉 200 篇验证解析率（质量门）
./.venv/bin/python fetch.py --game arknights               # 全量
KB_GAME=arknights tools/fetch_loop.sh                      # 反复续跑直到 manifest 标记抓完

# 阶段 2：解析（wikitext → SQLite 结构化表 + 语义 chunk）
./.venv/bin/python parse.py --game arknights
./.venv/bin/python tools/fetch_images.py --game arknights  # 图片文件名换直链（可选，只记 URL）
./.venv/bin/python tools/fetch_redirects.py --game arknights   # 重定向别名 → aliases 表

# 阶段 3：embedding → 向量库（Qdrant 单二进制，REST 6333）
./.venv/bin/python embed_index.py --game arknights --recreate
./.venv/bin/python -m kblib.bm25 --game arknights          # 重建 BM25 索引（parse 重跑后必做）
./.venv/bin/python tools/export_onnx.py --verify           # torch 权重 → ONNX int8（可选，一次即可）

# 阶段 4：评测
./.venv/bin/python eval.py --game arknights -v
```

环境：`kb/.venv`（mwparserfromhell / requests / onnxruntime / tokenizers / torch / PyYAML）。
数据按游戏分目录（`.gitignore` 已排除，只提交代码、配置和文档）：

| | azurlane（默认） | arknights |
|---|---|---|
| 配置 | `games/azurlane.yaml` | `games/arknights.yaml` |
| 数据目录 | `data/`（沿用 v1 起的布局） | `data/arknights/` |
| qdrant collection | `blhx_kb` | `arknights_kb` |
| chunk_id 前缀 | `blhx:` | `arknights:` |
| 题库 | `eval/questions.jsonl` | `eval/arknights/questions.jsonl` |

azurlane 的 `data_dir` 故意不搬家：`blhx_kb` 的向量、`index_meta.json`、bm25 索引、
53697 个 chunk_id 全在那儿，一搬家 v2/v3 的成果全部作废。新游戏各自一个目录，互不污染。

配置里有什么：wiki/api 地址与 UA、抓取礼貌参数（rps/批大小/超时/重试/分片页数）、
chunk 规则（长短阈值、台词字段后缀、每页图片数）、infobox 模板白名单（模板名 → kind/category，
支持前缀家族）、标题模式（长得像关卡号就算关卡）、导航/噪音模板清单、`parse.kinds`
（通用实体路的字段→列名映射）。碧蓝航线另挂 `extractor_module: kblib.extractors.azurlane`
（舰娘/装备/关卡这些字段语义特异的族走专用抽取器），明日方舟 8 族全走通用实体路，零代码。

## Pipeline 说明

### 1. fetch.py —— 抓取

MediaWiki API 批量拉 wikitext，只拉文本不下载图片。几个为"随时可被杀"设计的点：

- **分片自愈续拉**：断点以磁盘分片为准，不信任 manifest。重启后扫 `data/raw/pages-*.jsonl.gz`
  重建已完成标题集合；发现重复标题/坏行自动去重紧凑重写。历史事故（三个 run 重复拉同一批
  标题、manifest 谎报 4500 篇）就是靠这个自愈的。
- **小批量 + 长超时 + 指数退避**：单批 20 个标题（50 个会被大页面拖到 curl 超时 exit 28），
  单请求 180s，最多重试 6 次，退避 `min(2^attempt, 60)s`。
- **大页面二分隔离**：某页反复超时就把批次一拆二递归定位，只剩单篇还失败就记进
  `manifest.failed_titles` 放弃（碧蓝海事局WIKI讨论区是 Flow 页，API 返回
  MWUnknownContentModelException，永远拉不到）。
- **原子写**：分片和 manifest 都是先写 `.tmp` 再 `replace`，被 kill 只会留下完整的旧文件或新文件。

### 2. parse.py —— 解析

两条结构化路 + 一条文本路（外加 v3 的六类补捞和两条零成本增强，见下）：

- **结构化路·专用抽取器**（`kblib/extractors/azurlane.py`，由配置的 `extractor_module` 挂上）：
  碧蓝航线的舰娘/装备/指挥喵/关卡图鉴/原型舰性能数据/塞壬图鉴/同人角色 → SQLite `data/kb.db`，
  表：`ships / equipments / skills / ship_stats / ship_slots / cats / stages / ship_perf /
  sirens / fan_ships / pages / parse_stats / aliases / page_images`。
  字段语义和表结构是这个游戏特有的，写死在代码里更清楚。查表类问答直接走这里，精确匹配判分。
- **结构化路·通用实体**（`kblib/extractors/__init__.py` 的 `generic_entity`，零代码）：
  字段即结构的 infobox（明日方舟 CharinfoV2/敌人信息/道具信息/关卡信息/家具信息/装置信息/
  收藏品/VoiceTable 共 8 族）→ `entities`（一行一页，全字段进 `fields_json`）+
  `entity_attrs`（扁平字段一行一个）+ YAML 声明的子表（`ark_operator_stats`、
  `ark_operator_talents`、`ark_operator_skill_levels`、`ark_enemy_levels`、
  `ark_stage_enemies`… 表名和列名都在 `games/arknights.yaml` 的 `parse.kinds` 里）。
  编号字段（`天赋N`/`技能N描述`/`敌人N`）按正则拆行，`{0}` 是捕获组、`@key` 是"值即字段本身"。
  kind 同时出现在两边时专用抽取器优先。
- **文本路**：正文清洗（去导航模板、编辑标记、分类、"最后编辑于"噪音）→ 按语义切 chunk
  （一段讲一件事，`heading` 保留层级）→ `data/chunks.jsonl`，每行
  `{chunk_id, text, source_title, source_url, heading, kind, images?}`，`kind=card` 的是
  infobox 结构化摘要（数值/技能名都在这），`kind=prose` 的是正文段落。

v3 覆盖率审计（`docs/kb-coverage-audit.md`，漏了什么/补了什么/为什么不补）查出 v2 解析
整体丢掉六类内容，逐类补捞，**老 chunk 一个不动、只新增**：

- `kind=template`：通用模板兜底从"只取无名参数 1"改全参输出，`{{世界巡游}}`（整页 100 级
  奖励表）、`{{每月签到}}` 这类数据型模板不再只剩一行；
- `kind=fields`：infobox 一被抽取就从正文消失，>120 字的长字段（如 `META图鉴.介绍`）单独
  成 chunk，不再只躺在 `fields_json` 里检索不到；
- `kind=voice`：`*台词` 字段按"场景：台词"逐条入向量库（v2 只进了 SQLite）；
- `kind=gallery`：`<gallery>` 图注（画师名、日期、装备名）以"文件名 说明"成对保留；
- `kind=hidden`：HTML 注释里编辑藏起来的旧版正文（>800 字）整段捞回；
- `kind=card`（新族）：关卡图鉴/原型舰/塞壬/同人角色的卡片摘要，走 `x` 前缀 chunk_id，
  不挤占 v2 老 id。

另外两条零成本增强：

- **图片直链**：解析时把页面引用的文件名经 `prop=imageinfo&iiprop=url` 批量换成真实 URL
  （`tools/fetch_images.py`，结果落 `data/image_urls.json` 缓存，**只记 URL 不下载**），
  写进 chunk payload 的 `images` 字段（每页最多 16 张）+ `page_images` 表，
  bot 回答时可直接甩 wiki 原链配图。
- **重定向别名**：759 个别名页进 `aliases` 表，别名本身也能被检索到（内容在目标页里）。

### 3. embed_index.py —— 向量索引

- 模型：`BAAI/bge-small-zh-v1.5`（512 维，CLS 池化 + L2 归一化，query 加检索指令前缀）。
  两个后端接口一致：`torch`（transformers 直出，不依赖 sentence-transformers）和
  `onnx`（onnxruntime + int8 动态量化，`tools/export_onnx.py` 从 torch 权重导出，逐条余弦
  对齐校验 ≥0.99……int8 量化后约 0.96，top-k 重合度不受影响）。
- 本机（2 核）实测 torch fp32 ≈28 chunks/s，onnx int8 ≈86 chunks/s（短文本）；
  长文本（chunk p50 ≈341 token）降到 ~10 chunks/s，**全量索引约 2.5-3 小时**。
  索引进内存前按文本长度排序，批内 padding 浪费最小（随机序多花 ~1.5x）。
- 存储：Qdrant 单二进制（`kblib/qdrant_server.py` 负责起停），**一个进程服务所有游戏，
  collection 按游戏分**（`blhx_kb` / `arknights_kb`），512 维 cosine；
  `--store numpy` 可切纯 numpy 兜底（接口抽象在 `kblib/retriever.py`）。
- BM25 关键词索引：`kblib/bm25.py`（jieba 分词 + Okapi BM25，自实现无第三方检索库），
  落 `data/bm25/`，重建约 2 分钟。**parse.py 重跑后必须重建**
  （`./.venv/bin/python -m kblib.bm25`），否则混合检索的关键词那一半是旧 chunk 集。
- 混合检索（v2 起，`kblib/retriever.py` 的 `HybridStore`）：向量召回 + BM25 召回各 20 条
  → RRF 融合 → `bge-reranker-v2-m3` cross-encoder 精排出 top-k。

### 4. eval.py —— 评测

题库在游戏配置的 `eval_dir/questions.jsonl`，两类判分：

- **table（查表）**：探针查 `kb.db`，归一化（NFKC、全角括号、去空白、小写）后精确比对。
  探针两套：碧蓝航线的 `skill_name/stat/ship_field/slot_equip…`（专用表），
  和通用实体路的 `entity_attr/entity_json/child/child_list`（`child` 的表名列名白名单
  从配置的 `parse.kinds` 来，题库里写不存在的表/列会直接报错）。
  标准答案全部用 `tools/raw_lookup.py` 回查过原始 wikitext，不经过 parser 自证。
- **qa（检索）**：向量检索 top3，`expect_keywords` 全部命中才算过（考检索+切分，不考生成）。

## 数据规模与评测结果

| 指标 | 数值 |
|---|---|
| 抓取篇数 | 18573 / 18574（1 篇 Flow 页放弃，见 manifest.failed_titles） |
| 原始 wikitext | ~30 MB（39 个 gzip 分片） |
| 带 infobox 页 | 4399 |
| **结构化抽取率** | **99.9%**（占带 infobox 页；全页占比 23.6%） |
| chunk 数 | 53697（prose 31569 / card 4365 / template 8189 / fields 3696 / voice 3004 / gallery 2137 / hidden 737） |
| 向量数 | 53697（qdrant collection `blhx_kb`，与 chunks.jsonl 行数一致；v2 尾部没 flush 的 138 个超长 chunk 已补齐） |
| SQLite 表 | 14 张（v2 的 8 张 + `stages` / `ship_perf` / `sirens` / `fan_ships` / `aliases` / `page_images`） |
| data/ 磁盘占用 | ~600 MB（原始 30 + kb.db 29 + chunks 91 + 向量库 330 + 模型另计 ~121 MB） |
| 页面类型分布 | ship 953 / equipment 1152 / stage 2000 / ship_perf 105 / siren 93 / fan_ship 78 / cat 34 / other 14158 |

**eval 通过率**：
- v1（2026-10-19，纯向量 top-3）：查表 30/30 = 100%；问答 9/20 = 45%；总计 39/50 = 78%
- v2（2026-10-19，BM25 jieba + RRF 混合 + bge-reranker-v2-m3 精排，top-20→top-5）：
  查表 30/30 = 100%；问答 15/21 = 71%（+回归题"举高高是谁"✓）；**总计 45/51 = 88.2%**
  （明细见 `data/eval_report_v2.json`）。
- 说明：问答题测的是纯检索召回（关键词命中），偏严格；真实 bot 走"查表优先、检索兜底"时，
  事实类问题（q01/q03/q08 这类）会被查表路径接住，实际体验高于 88%。
  剩余检索短板主要是装备数值/配装指南类长尾（q07/q17/q18）。

infobox 模板分布：舰娘图鉴 953、舰炮图鉴 298、特殊兵装图鉴 259、设备图鉴 216、
舰载机图鉴 177、防空炮图鉴 120、鱼雷图鉴 80、指挥喵图鉴 34、导弹图鉴 2；
v3 新增 4 族：关卡图鉴 1984、塞壬图鉴 93、飞行器性能数据 88、同人角色 78、
原型舰性能数据 16、原型舰性能数据（潜艇）1（聚合/导航模板 `装备图鉴` 827、`关卡图鉴汇总` 196
不抽，明细见 `docs/kb-coverage-audit.md` 第 4 节）。

### 明日方舟（arknights）

| 指标 | 数值 |
|---|---|
| 抓取篇数 | 19740（非重定向正文全量落盘，43 个 gzip 分片 / 40 MB） |
| 重定向别名 | 7691（进 `aliases` 表，qa_demo 的实体路由用） |
| 带 infobox 页 | 12185（结构化抽取率 100%，全页占比 61.7%） |
| **chunk 数** | **120114**（prose 13870 / card 12185 / template 77081 / fields 15652 / hidden 1322 / gallery 4） |
| **向量数** | **120114**（qdrant collection `arknights_kb`，与 chunks.jsonl 行数一致） |
| SQLite 表 | 27 张（`entities` 12185 / `entity_attrs` 126937 + `ark_operator_*` 11 张等 21 张子表） |
| 页面类型分布 | stage 4715 / furniture 2877 / other 6506 / enemy 1832 / item 1392 / device 775 / collectible 741 / operator 463 / voice 439 |

混合检索三件套已在方舟库上配齐（逻辑从碧蓝航线继承，`--game` 一换即用，零代码改动）：
BM25 索引 **120114 docs / 188010 词条**（jieba 分词，232s 建完，84.7 MB，落 `data/arknights/bm25/`），
向量召回与 BM25 召回各 20 条 → RRF 融合 → `bge-reranker-v2-m3`（ONNX int8，本地 561 MB 权重）
精排出 top-k。冒烟实测单问端到端 38s（含 jieba 与 reranker 冷启），reranker 对首位命中的
打分能把 `卡片摘要` 噪声块压到尾段。

## 开发小工具

```bash
./.venv/bin/python tools/raw_lookup.py 企业 拉菲 --grep "技能1名"   # 回查原始 wikitext
./.venv/bin/python tools/export_onnx.py --verify                     # 重新导出 ONNX int8
tools/fetch_loop.sh                                                  # 断点续拉直到抓完
tools/embed_resume.sh arknights                                      # embedding 断点续跑（见下）
```

`tools/embed_resume.sh` 是 `embed_index.py --append` 的长任务启动壳，比裸跑 python 多两件事：
把 `oom_score_adj` 设成 1000（2 核 7G 的机器上内存见底时让 kernel 先杀自己，别误伤常驻的
qdrant 或旁边 agent 的进程），以及 `python -u`（逐批进度实时落盘，被 kill 后能看出死在哪一批）。
方舟这轮 120114 个 chunk 就是靠它三次续跑补齐的：被机器重启杀掉后重跑同一条命令，
`--append` 按 chunk_id 对账，库里已有且文本一致的一个向量都不重算。
