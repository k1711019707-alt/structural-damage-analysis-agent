## Why

层级化召回和语义分数已经在 `knowledge_pipeline` 输出，但运行时门面仍将结果压缩为旧的 `KnowledgeBaseChunk`，导致 GUI 报告和施工方案生成无法使用 anchor、expanded、context_groups 和语义分数。重排器也仍只按原始顺序计算词面重叠，不能识别语义命中和扩展上下文的重要性。

## What Changes

- 让运行时知识库检索门面保留层级召回诊断，并把结构化上下文传递到生成上下文。
- 扩展运行时生成上下文，保留 `anchors`、`context_groups`、检索模式和证据边界。
- 让 `rerank.py` 使用 semantic score、fusion score、anchor/expanded 状态和章节/标题重叠进行确定性重排。
- 保留旧的 `KnowledgeBaseSearchResult` 字段和 legacy 数据库回退，避免破坏现有 GUI 调用。

## Capabilities

### New Capabilities
- `runtime-hierarchical-context`: 运行时门面和生成层的层级证据传递。
- `structure-aware-reranking`: 结合语义和层级诊断的确定性重排。

### Modified Capabilities

## Impact

影响 `runtime/knowledge_base.py`、`runtime/generation_context.py`、`runtime/damage_workflow_gui.py` 的兼容接入，以及 `knowledge_pipeline/rerank.py`、contracts 和测试。不会改变 PDF、chunk 或 index 数据格式；旧调用方仍可读取 chunks。
