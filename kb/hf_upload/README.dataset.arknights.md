---
license: cc-by-nc-sa-3.0
language:
- zh
tags:
- knowledge-base
- retrieval-augmented-generation
- arknights
- game-wiki
---

# 明日方舟知识库（PRTS wiki 炼制）

从 [明日方舟 PRTS wiki](https://prts.wiki) 全量炼制的 bot 可用知识库。
数据源遵循原站 CC BY-NC-SA 协议，本数据集同样以该协议发布，仅限非商业用途。

## 内容

| 产物 | 说明 |
|---|---|
| `raw/` | 19740 篇 wikitext 原文（gzip 分片 + manifest） |
| `chunks.jsonl` | 清洗后语义 chunk，120114 条（含标题/链接/图片 URL） |
| `kb.db` | 结构化表（SQLite）：12185 实体（干员/敌人/道具/关卡等 8 族，通用实体路） |
| `bm25/` | BM25 索引（jieba 分词，120114 docs / 188010 词条） |
| `qdrant/` | 向量库快照（bge-small-zh，120114 向量，可直接加载） |
| `eval_report.json` | 评测明细 |

## 评测

- 查表：30/30 = 100%
- 问答检索：16/20 = 80%（BM25 jieba + RRF + bge-reranker-v2-m3）
- 总计：46/50 = 92.0%

## 拉取

```python
from huggingface_hub import snapshot_download
p = snapshot_download("tianyin231/arknights-kb", repo_type="dataset")
# p/arknights/chunks.jsonl, p/arknights/kb.db, ...
```

## 备注

- `{{人员档案set}}` 模板（种族/性别/出身地等字段）尚未解析进 entity_attrs，下一轮 parse 补。
- 模型权重不随数据集发布（bge-small-zh-v1.5 / bge-reranker-v2-m3 均为公开下载）。
