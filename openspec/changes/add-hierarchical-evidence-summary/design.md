## Context

当前 v2 检索输出 retrieval child，并在 child 后 hydration 一个 context-only parent。child 已含 `heading_path`、`clause_number`、`page_numbers`、`part`、来源 marker 和质量字段，但没有将同一小节的多个 child 组合成可供 AI 理解的证据组。`generate.py` 当前只把 chunks 原样包装为生成上下文，没有针对裂缝损伤的证据约束模板。

## Goals / Non-Goals

**Goals:**

- 在不改变上游切片和索引 schema 的情况下，按 heading_path/章节层级扩展检索上下文。
- 区分 semantic/lexical 命中的 anchor 与为完整语境加入的 expanded 内容。
- 保留原文顺序、来源和质量信息，并严格限制上下文长度。
- 为裂缝、剥落、变形等损伤问题生成结构化、证据约束的 AI 提示上下文。

**Non-Goals:**

- 不把整本知识库或无关章节发送给模型。
- 不自动判断结构安全等级、不替代工程师复核。
- 不在本变更中重写 `pdf_convert.py`、`chunk.py`、`index.py`。
- 不引入新的外部模型调用；本变更只构建提示和结构化上下文。

## Decisions

1. **扩展层级使用 `heading_path`。** 对 anchor 的 heading path 进行最长公共前缀分组；`auto` 模式优先同小节，证据不足或显式请求时再扩展到上一级章节。没有 heading path 的表格/图片按 document/page 邻域保守扩展。
2. **先 anchor、再扩展、后排序。** anchor 由原始混合召回确定；扩展内容从同文档 retrieval child 和已 hydration parent 中选择，不改变 anchor 的排名；最终上下文按原文位置排序。
3. **结果增加非破坏性字段。** `RetrievalResult` dataclass 保持兼容，额外字段放在 `retrieved_chunks` 的 metadata 和动态 payload 字段中，旧调用方继续读取 `chunks`。
4. **上下文预算采用字符硬上限。** 保留所有 anchor，剩余预算优先补同小节 parent/child，再按章节顺序补充；截断时保留来源 marker 和 expansion 标记。
5. **生成模式采用固定证据约束模板。** `damage-grounded-summary` 要求模型分离“证据事实、可能解释、建议复核、处理建议、限制和引用”，证据不足时明确说明并保留 `pending_engineer_review`。

## Risks / Trade-offs

- 小节扩展可能带入弱相关内容 → 默认只扩展同一 heading_path，使用字符预算和 anchor 优先级限制。
- heading_path 缺失或 OCR 错误 → 回退到 parent/page 邻域，并在 context group 中标记 expansion_reason。
- 上下文截断可能丢失条款尾部 → anchor 和 parent 不截断优先，扩展块按顺序裁剪并保留来源。
- 模型可能把建议当成规范结论 → 生成提示强制区分事实、推断、建议和不确定性。

## Migration Plan

1. 仅更新检索和生成 JSON 输出，旧调用方继续使用 `chunks`。
2. 先用测试 payload 验证同小节扩展、章节回退、预算和引用。
3. 在 GUI/Responses 服务中逐步读取 `context_groups` 与 `generation_mode`，出现问题时可退回旧 chunks-only 行为。

## Open Questions

- 章节扩展阈值和跨小节合并规则需要真实工程评测集调参。
- 后续是否将 context_groups 固化到 contracts.py dataclass，取决于 GUI 是否需要强类型访问。
