# RAG 基准数据集

本目录包含两套基准资产：

- `document_benchmark.jsonl`：真实生产 v2 文档的解析、页、chunk、OCR/质量警告和推荐复核页清单。
- `retrieval_qa_benchmark.jsonl`：检索问答候选集，覆盖自然中文、同义词、标准号、条款号、表格、跨页、无答案、范围隔离和工程师复核边界。
- `manifest.json`：生成时间、生产库路径、数据库 SHA-256 和记录数量。
- `production_query_expectations.json`：生产候选库必须通过的代表性查询门禁；每条查询都绑定预期文档、标准号或来源文件名，不以“返回非空”代替相关性验证。
- `production_scope_expectations.json`：由当前 GUI 配置和 legacy catalog 核对出的损伤分析报告、施工方案范围门禁。
- `formal_v1/`：基于最新测试流水线快照生成的 150 题正式候选包，包含固定题型配额、页级证据、人工复核模板和 Gold 发布门禁。该目录当前仍是 `needs_human_review`，不是已完成的 Gold 集。

## 150 题正式候选包

从项目根目录运行：

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\formal_rag_benchmark.py generate
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\formal_rag_benchmark.py validate
```

输出位于 `benchmarks/rag/formal_v1/`：

- `candidates.jsonl`：150 条不可直接冒充 Gold 的自动候选；
- `review_template.jsonl` / `review_template.csv`：独立人工复核副本；
- `catalog.md`：题目、类型、难度和证据页目录；
- `manifest.json`：SQLite/PDF 哈希、类型配额和来源文档清单；
- `coverage_report.json` / `coverage_report.md`：结构校验和覆盖统计；
- `REVIEW_GUIDE.md`：逐页核验及 Gold 发布流程。

默认绑定 `knowledge_pipeline/test/results/index/pipeline.sqlite3`，不会读取当前已被新契约判定为不兼容的旧生产库作为正式基准真值，也不会修改输入 SQLite、PDF、活动 manifest 或旧的 21 条候选文件。

## 重新生成

从项目根目录运行：

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts/generate_rag_benchmarks.py
```

脚本只读当前激活的生产 v2 数据库和源文件元数据，不修改生产数据库、旧库或 PDF。若数据库发生重建，重新生成后应检查 `manifest.json` 中的 `database_sha256` 是否变化。

## 标注边界

`retrieval_qa_benchmark.jsonl` 中的 `candidate_chunks`、`relevant_documents` 和 `relevant_chunk_ids` 是自动候选（其中 `relevant_*` 默认取直接 anchor 命中，不包含扩展上下文），不是最终金标准；每条记录默认 `annotation_status: needs_human_review`，只有人工核对原 PDF、页码和条款后才能把 `gold_label` 改为 `true`。

`expected_no_answer=true` 的问题用于测试知识库拒答/无证据边界，不应因为 scoped fallback 返回了内容就标为命中。`expected_engineer_review=true` 的问题用于检查生成层是否保留工程师复核边界。

## 生产候选库门禁

`production_query_expectations.json` 和 `production_scope_expectations.json` 是外部、可审计的激活输入，不是自动生成的金标准。当前范围文件对应 2026-09-17 核对的 GUI 配置：损伤分析报告使用“分析报告”文件夹，施工方案使用“施工”文件夹；验证器会将文件夹与显式文档 ID 取交集，并要求选中范围在候选 v2 中可用。

GUI 范围或 legacy catalog 变化时，必须先重新核对并更新范围文件，再执行候选验证；不得沿用过期范围，也不得只修改验证报告来迁移激活输入。

## 建议评测字段

用人工完成 `relevant_chunk_ids` 后，可以计算 Hit@K、Recall@K、Precision@K、MRR、nDCG、scope leakage rate 和 p50/p95 延迟。文档基准可计算页面分类、OCR/表格质量、切片覆盖率、parent-child 关联率和索引完整性。
