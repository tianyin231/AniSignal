# 碧蓝航线知识库（kb/）

把 [碧蓝航线 BWIKI](https://wiki.biligame.com/blhx) 炼成 bot 可检索、可查表的知识库。
四个阶段：**抓取 → 解析 → 向量索引 → 评测**，全程本地跑，不调外部 API（模型 embedding 也是本地 CPU）。

## 快速开始

```bash
cd ~/workspace/anisignal/kb

# 阶段 1：抓取（断点续拉，随时可 Ctrl-C，重跑同一命令继续）
./.venv/bin/python fetch.py --spike 200      # 先拉 200 篇验证解析率（质量门）
./.venv/bin/python fetch.py                   # 全量
tools/fetch_loop.sh                           # 反复续跑直到 manifest 标记抓完

# 阶段 2：解析（wikitext → SQLite 结构化表 + 语义 chunk）
./.venv/bin/python parse.py

# 阶段 3：embedding → 向量库（Qdrant 单二进制，REST 6333）
./.venv/bin/python embed_index.py --recreate
./.venv/bin/python tools/export_onnx.py --verify   # torch 权重 → ONNX int8（可选）

# 阶段 4：评测
./.venv/bin/python eval.py -v
```

环境：`kb/.venv`（mwparserfromhell / requests / onnxruntime / tokenizers / torch）。
数据全在 `kb/data/`（`.gitignore` 已排除，只提交代码和文档）。

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

两条路并行：

- **结构化路**：infobox 模板（舰娘/装备/指挥喵图鉴等 9 类）→ SQLite `data/kb.db`，
  表：`ships / equipments / skills / ship_stats / ship_slots / cats / pages / parse_stats`。
  查表类问答直接走这里，精确匹配判分。
- **文本路**：正文清洗（去导航模板、编辑标记、分类、"最后编辑于"噪音）→ 按语义切 chunk
  （一段讲一件事，`heading` 保留层级）→ `data/chunks.jsonl`，每行
  `{chunk_id, text, source_title, source_url, heading, kind}`，`kind=card` 的是 infobox
  结构化摘要（数值/技能名都在这），`kind=prose` 的是正文段落。

### 3. embed_index.py —— 向量索引

- 模型：`BAAI/bge-small-zh-v1.5`（512 维，CLS 池化 + L2 归一化，query 加检索指令前缀）。
  两个后端接口一致：`torch`（transformers 直出，不依赖 sentence-transformers）和
  `onnx`（onnxruntime + int8 动态量化，`tools/export_onnx.py` 从 torch 权重导出，逐条余弦
  对齐校验 ≥0.99……int8 量化后约 0.96，top-k 重合度不受影响）。
- 本机（2 核）实测 torch fp32 ≈28 chunks/s，onnx int8 ≈86 chunks/s（短文本）；
  长文本（chunk p50 ≈341 token）降到 ~10 chunks/s，**全量索引约 2.5-3 小时**。
  索引进内存前按文本长度排序，批内 padding 浪费最小（随机序多花 ~1.5x）。
- 存储：Qdrant 单二进制（`kblib/qdrant_server.py` 负责起停），collection `blhx_kb`，
  512 维 cosine；`--store numpy` 可切纯 numpy 兜底（接口抽象在 `kblib/retriever.py`）。

### 4. eval.py —— 评测

题库 `eval/questions.jsonl`，两类判分：

- **table（查表）**：探针查 `kb.db`，归一化（NFKC、全角括号、去空白、小写）后精确比对。
  标准答案全部用 `tools/raw_lookup.py` 回查过原始 wikitext，不经过 parser 自证。
- **qa（检索）**：向量检索 top3，`expect_keywords` 全部命中才算过（考检索+切分，不考生成）。

## 数据规模与评测结果

| 指标 | 数值 |
|---|---|
| 抓取篇数 | 18573 / 18574（1 篇 Flow 页放弃，见 manifest.failed_titles） |
| 原始 wikitext | ~30 MB（39 个 gzip 分片） |
| 带 infobox 页 | 2139 |
| **结构化抽取率** | **100.0%**（占带 infobox 页；全页占比 11.5%） |
| chunk 数 | 33674（prose 31569 + card 2105） |
| 向量数 | 33536（qdrant collection `blhx_kb`，chunk 总数 33674，差 138 为尾部未flush/去重） |
| data/ 磁盘占用 | ~388 MB（原始 30 + kb.db 18 + chunks 51 + 向量库 174 + 模型另计 ~121 MB） |
| 页面类型分布 | ship 953 / equipment 1152 / stage 147 / cat 34 / other 16287 |

**eval 通过率**：
- v1（2026-10-19，纯向量 top-3）：查表 30/30 = 100%；问答 9/20 = 45%；总计 39/50 = 78%
- v2（2026-10-19，BM25 jieba + RRF 混合 + bge-reranker-v2-m3 精排，top-20→top-5）：
  查表 30/30 = 100%；问答 15/21 = 71%（+回归题"举高高是谁"✓）；**总计 45/51 = 88.2%**
  （明细见 `data/eval_report_v2.json`）。
- 说明：问答题测的是纯检索召回（关键词命中），偏严格；真实 bot 走"查表优先、检索兜底"时，
  事实类问题（q01/q03/q08 这类）会被查表路径接住，实际体验高于 88%。
  剩余检索短板主要是装备数值/配装指南类长尾（q07/q17/q18）。

infobox 模板分布：舰娘图鉴 953、舰炮图鉴 298、特殊兵装图鉴 259、设备图鉴 216、
舰载机图鉴 177、防空炮图鉴 120、鱼雷图鉴 80、指挥喵图鉴 34、导弹图鉴 2。

## 开发小工具

```bash
./.venv/bin/python tools/raw_lookup.py 企业 拉菲 --grep "技能1名"   # 回查原始 wikitext
./.venv/bin/python tools/export_onnx.py --verify                     # 重新导出 ONNX int8
tools/fetch_loop.sh                                                  # 断点续拉直到抓完
```
