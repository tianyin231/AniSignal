---
license: cc-by-nc-sa-3.0
language:
- zh
tags:
- knowledge-base
- retrieval-augmented-generation
- azur-lane
- game-wiki
---

# 碧蓝航线知识库（BWIKI 炼制）

从 [碧蓝航线 BWIKI](https://wiki.biligame.com/blhx) 全量炼制的 bot 可用知识库。
数据源遵循原站 CC BY-NC-SA 协议，本数据集同样以该协议发布，仅限非商业用途。

## 内容

| 产物 | 说明 |
|---|---|
| `raw/` | 18573 篇 wikitext 原文（gzip 分片 + manifest） |
| `chunks.jsonl` | 清洗后语义 chunk，33674 条（含标题/链接/图片 URL） |
| `kb.db` | 结构化表（SQLite）：ships 953 / skills 2599 / equipments 1152 / ship_stats 8959 |
| `bm25/` | BM25 索引（jieba 分词） |
| `qdrant/` | 向量库快照（bge-small-zh，33536 向量，可直接加载） |
| `eval_report_v2.json` | 评测明细 |

## 评测（v2）

- 查表：30/30 = 100%
- 问答检索：15/21 = 71%（BM25 jieba + RRF + bge-reranker-v2-m3）
- 总计：45/51 = 88.2%

## 拉取

```python
from huggingface_hub import snapshot_download
p = snapshot_download("tianyin231/azurlane-kb", repo_type="dataset")
# p/azurlane/chunks.jsonl, p/azurlane/kb.db, ...
```

pipeline 代码见 GitHub `tianyin231/anisignal` 的 `feat/wiki-kb-azurlane` 分支（`kb/` 目录）。
