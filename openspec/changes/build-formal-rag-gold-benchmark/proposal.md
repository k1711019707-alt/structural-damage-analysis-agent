## Why

项目现有 `benchmarks/rag` 仅有 21 条自动检索候选，无法支撑 Recall、MRR、nDCG、无答案能力、范围隔离和回答忠实度的正式量化。当前最新流水线已经产生可追溯的 PDF、切片、索引和检索结果，适合建立一套固定版本、类型平衡、可人工复核的 150 题基准快照。

## What Changes

- 新增一个只读的正式基准生成与校验工具，绑定指定 SQLite 快照和项目内原始 PDF，不修改知识库或生产数据库。
- 生成 150 条类型配额固定的 RAG 问答记录：精确条款/标准编号 25、自然语言改写 35、数值和单位 25、表格查询 20、跨页/多证据 15、文档范围隔离 10、无答案 10、风险/否定/例外条件 10。
- 每条可回答记录保留文档、页码、直接证据 chunk、证据文本、候选答案、必备事实、禁止错误、风险等级和溯源哈希。
- 自动生成记录统一标记为待人工复核且非 Gold；提供独立人工复核副本、标注说明和发布校验，不覆盖自动候选。
- 输出机器可读 JSONL/JSON、便于人工审核的 CSV/Markdown 和覆盖统计报告。

## Capabilities

### New Capabilities

- `formal-rag-benchmark`: 定义 150 题正式基准候选集、溯源字段、配额、人工晋级 Gold 边界和一致性校验。

### Modified Capabilities

- 无。现有小规模候选文件保持兼容，新能力以独立版本目录提供。

## Impact

- 新增/扩展 `scripts/` 下的基准生成与校验脚本、`benchmarks/rag/formal_v1/` 数据资产及相应测试。
- 只读访问 `knowledge_pipeline/test/results/index/pipeline.sqlite3`、当前解析/切片产物和项目内源 PDF。
- 不修改检索算法、生产数据库、活动 manifest、原 PDF 或已有 21 条候选文件。
