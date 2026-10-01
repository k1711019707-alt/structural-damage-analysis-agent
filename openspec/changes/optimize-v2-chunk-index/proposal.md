## Why

新版 `chunk.py` 已输出 parent/child、表格行、图片 OCR、章节/条款、来源和质量元数据，但当前 `index.py` 仍把所有记录当作扁平文本写入单一 FTS5 表，导致上下文块污染普通检索、结构字段无法过滤或加权、增量重建成本高，并且容易在检索时产生 parent/child 重复命中。现在需要让索引层完整承接 `knowledge-chunks.v2`，为后续高召回率检索和重排保留可解释证据。

## What Changes

- 重构 `index.py` 的 SQLite schema，保存文档、页面、chunk、parent-child 关系、表格和图片 OCR 的结构化元数据。
- 将 `text_search`、章节标题、条款号、标准编号、表格文本和图片 OCR 分列写入加权 FTS5；普通检索默认只索引 `retrieval_role=\"retrieval\"` 的记录。
- 保留完整的原始文本、上下文化文本、来源标记、页码、提取方式、质量标记和 `needs_review`，不因质量问题静默丢弃证据。
- 增加内容 hash、重复组和文档源 SHA-256，实现同文档幂等重建与批量增量索引。
- 增加页面/表格/图片统计及索引 manifest，使用事务和 SQLite WAL 提高批量写入性能。
- 对 `retrieve.py` 做最小兼容适配：过滤 context-only parent、按 child 命中并可补 parent、读取 metadata，同时保留旧数据库适配器。

## Capabilities

### New Capabilities
- `v2-chunk-indexing`: 将结构感知的 v2 切片完整、可增量地写入 SQLite/FTS5 索引。
- `structure-aware-retrieval-adapter`: 在现有 lexical 检索接口中利用 retrieval role、结构字段和 parent-child 关系。

### Modified Capabilities

## Impact

影响 `knowledge_pipeline/index.py`、`knowledge_pipeline/retrieve.py`、`contracts.py` 中的索引版本标记及知识库 pipeline 测试。默认使用 SQLite/FTS5、Python 标准库和项目现有依赖，不引入向量数据库或新的 embedding 依赖；旧 `documents/chunks/chunks_fts` 入口继续保留。建议输出到新的 v2 数据库文件，避免覆盖历史知识库。
