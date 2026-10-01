## Why

150 题正式基准候选集已由项目负责人完成原 PDF 人工核对，可以晋级为版本化 Gold Set。项目目前缺少在同一 Gold、同一 SQLite/向量快照和同一 scope 条件下对词法、语义、混合、重排及层级上下文的可复现量化比较。

## What Changes

- 根据用户本轮人工核对声明生成独立审核记录，并通过既有发布门禁生成 Gold Set，不覆盖候选集。
- 新增只读 RAG 评测/消融运行器，调用现有词法检索、语义检索、混合检索、确定性重排和层级展开模块。
- 固定比较五组配置：词法结构化、纯语义、词法+语义、混合+重排、混合+重排+层级上下文。
- 计算 Hit@K、Recall@K、Precision@K、MRR@10、nDCG@10、无答案 Precision/Recall/F1、scope 泄漏、P50/P95 延迟和失败案例。
- 输出逐题 JSONL、汇总 JSON/CSV/Markdown、类型分层统计、配置/环境/哈希和可复现命令。

## Capabilities

### New Capabilities

- `rag-ablation-evaluation`: 定义 Gold 晋级声明、统一消融协议、指标计算、结果追踪和报告边界。

### Modified Capabilities

- 无。

## Impact

- 新增评测脚本、测试以及 `benchmarks/rag/formal_v1/gold_v1/` 和 `benchmarks/rag/formal_v1/evaluation_v1/` 资产。
- 只读访问当前 SQLite、语义 NPZ、Gold JSONL 和源文件，不修改检索模块、知识库、活动 manifest 或原 PDF。
- 语义实验会加载现有本地 `BAAI/bge-small-zh-v1.5` 模型并产生实际运行时间。
