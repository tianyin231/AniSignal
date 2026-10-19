# 炼 v2：混合检索 + rerank（2026-10-19）

## 背景
v1 问答检索是纯向量 top-3，eval 9/20=45%。真实翻车案例："举高高是谁"
向量检索 top-5 全是边角料（最高 0.49），答"不知道"；而关键词 grep 一秒找到
wiki 里的"举高高（埃尔德里奇）"。结论：必须上混合检索。

## 做什么（分支 feat/wiki-kb-azurlane，接着干）
1. **BM25 索引**：33674 个 chunk，中文用 jieba 分词，索引持久化到 `data/bm25/`（不进 git）。
2. **混合检索**：向量（qdrant）+ BM25，用 RRF（reciprocal rank fusion）融合，初排 top-20。
   检索接口保持抽象（kblib/retriever.py 里加 HybridStore 或扩展函数），方便以后换。
3. **Rerank**：本地 cross-encoder 精排（选 bge-reranker-v2-m3 级别的小模型，
   CPU 能跑，模型放 `data/models/` 不进 git），从 20 个里精排取 top-5。
4. **接入 qa_demo.py**：查表路由保持最高优先；查表未命中 → 混合检索+Rerank → 生成。
5. **回归题**：把"举高高是谁"加进 `kb/eval/questions.jsonl`（kind=qa，
   expect_keywords 含"埃尔德里奇"），以后这题必须过。
6. **重跑 eval**：`eval.py` 的 qa 部分改用新检索链路（top_k 语义保持可比），
   对比 v1（9/20）写进 README，目标 ≥14/20。

## 红线
- data/、data/models/、kb/.venv/ 不进 git（.gitignore 已有）。
- 每完成一步就 commit（中文一句话 message）。
- 完工报告：v1 vs v2 问答通过率对比、举高高回归题结果、commit hash。
