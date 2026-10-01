# Design: RAG 基准资产

## 数据来源

生成脚本读取 `runtime.rag_production.active_rag_status()` 获取当前激活 v2 数据库，再从 SQLite 读取文档、页面、chunk 和 FTS 统计。源文件路径只作为追溯信息写入清单，不复制或修改源文件。

## 输出

- `benchmarks/rag/document_benchmark.jsonl`：每行一个真实文档基准记录。
- `benchmarks/rag/retrieval_qa_benchmark.jsonl`：每行一个检索问答候选记录。
- `benchmarks/rag/README.md`：字段、标注流程、评测指标和复现命令。
- `benchmarks/rag/manifest.json`：数据库 SHA-256、生成时间、文档/查询数量和自动候选边界。

查询集由真实标准号、条款号、chunk 中的短语和工程场景模板组成。自动生成的相关 chunk 只作为候选，默认 `annotation_status: needs_human_review`；无答案和高风险问题的相关 chunk 为空，并要求人工确认。

## 质量边界

自动生成不会声称查询答案正确，也不会把 `scoped_fallback` 当作相关命中。评测脚本或后续人工标注应区分 `relevance_status`、`scope_document_ids`、`relevant_chunk_ids` 和 `needs_review`。
