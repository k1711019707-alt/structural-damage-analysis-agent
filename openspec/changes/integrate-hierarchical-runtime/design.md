## Context

`knowledge_pipeline.retrieve.retrieve()` 已返回 `anchors` 和 `context_groups`，并为每个 chunk 标记 semantic/lexical 通道、anchor/expanded 状态。运行时 `pipeline_search_with_scope()` 目前转换为只含五个字段的 `KnowledgeBaseChunk`，在转换时丢失 metadata；`runtime.generation_context` 也只接收旧 chunk 列表。`rerank.py` 仅按 query token overlap 排序。

## Goals / Non-Goals

**Goals:**

- 不破坏 GUI 当前使用的 `KnowledgeBaseSearchResult.chunks`、scope 和 fallback 字段。
- 在运行时保留层级召回和语义诊断，供生成 prompt、审计和 UI 使用。
- 让确定性重排综合 semantic/fusion score、anchor 状态、标题路径和原始来源位置。

**Non-Goals:**

- 不在本变更中新增模型或修改 embedding 索引。
- 不重写 GUI 的报告/施工方案 schema，不自动调用外部 LLM。
- 不改变 legacy 数据库结构和旧适配器的基本行为。

## Decisions

1. **扩展运行时 dataclass 而非替换。** `KnowledgeBaseChunk` 增加可选 metadata 字段；`KnowledgeBaseSearchResult` 增加 anchors/context_groups 默认空值，旧构造代码继续有效。
2. **生成上下文保留动态证据字段。** `runtime.generation_context.GenerationContext` 增加 anchors/context_groups，manifest 也记录摘要，不把大段重复文本写入 manifest。
3. **重排采用有界确定性分数。** semantic_score 归一化后加权，fusion_score、anchor、同 heading_path 和 lexical overlap 作为特征；expanded 只用于上下文，不应压过 anchor。
4. **兼容旧库。** legacy 检索返回空层级字段和原有分数；运行时只在 pipeline 结果含 metadata 时填充扩展信息。

## Risks / Trade-offs

- dataclass 字段增加会影响序列化 → 全部提供默认值并保持旧位置参数顺序。
- 语义分数与 BM25 量纲不同 → 使用有界 sigmoid/截断特征，保留原始分数审计。
- 扩展上下文增加 prompt 长度 → 生成层只把 context_groups 作为结构化诊断，正文仍使用已裁剪 chunks。

## Migration Plan

1. 先升级运行时 dataclass 和门面，旧数据库保持原行为。
2. 升级 rerank 和 generate 的 JSON 兼容字段。
3. 运行专项测试和全量测试；异常时可只关闭扩展字段，不影响核心检索。

## Open Questions

- GUI 是否需要显式展示 context_groups，后续根据交互需求决定。
- 章节距离特征需要真实标注集调权，本变更先使用稳定启发式。
