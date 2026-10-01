## Why

当前混合召回已经能找到与裂缝损伤相关的 child，但返回结果仍是按命中顺序排列的孤立片段，AI 缺少同一小节中的前置条件、评定规则、表格和处理要求。需要把召回锚点扩展为结构化上下文，并生成带引用、适用条件和不确定性边界的损伤总结提示。

## What Changes

- 为 v2 检索增加自适应上下文扩展：先补 parent，再按 `heading_path` 聚合同小节，必要时扩展上一级章节。
- 将真正命中的 child 标记为 anchor，将扩展内容标记为 expanded，并保留扩展原因和锚点关联。
- 按文档、页码、条款号、part 和 chunk_id 稳定排序，受上下文字符预算约束。
- 扩展 `generate.py`，提供基于检索上下文的结构化损伤总结提示模式。
- 保留来源 marker、质量标记、needs_review 和 `pending_engineer_review`，不自动作出工程安全等级结论。

## Capabilities

### New Capabilities
- `hierarchical-evidence-context`: 从检索锚点组装小节/章节级证据上下文。
- `evidence-grounded-damage-summary`: 为裂缝/损伤场景构建证据约束的 AI 总结上下文。

### Modified Capabilities

## Impact

主要影响 `knowledge_pipeline/retrieve.py`、`knowledge_pipeline/generate.py` 及对应测试；不改变 PDF 转换、切片或 SQLite v2 schema。后续 GUI 可继续使用现有 `chunks` 字段，也可读取新增的 `anchors`、`context_groups` 和生成约束字段。
