## Context

v2 管线已经由 `pdf_convert.py` 和 `chunk.py` 生成带标题、条款、标准号、表格、图片 OCR、parent/child 关系和来源标记的结构化 chunk，`index.py` 已将这些字段写入 SQLite/FTS5。当前 `retrieve.py` 仅按空格和连续 Unicode 字符构造 OR 查询，连续中文自然问题因此成为不存在的长 token；数字条款号还会被过滤。

## Goals / Non-Goals

**Goals:**

- 提高连续中文自然问题的确定性召回率。
- 精确支持标准号和条款号查询，并兼容空格、连字符和“第…条”写法。
- 保持 SQLite/FTS5、作用域隔离、child-only top-k、parent hydration 和旧库适配器。
- 用多通道候选并集和可解释融合替代单次 FTS 查询。

**Non-Goals:**

- 本变更不引入 embedding、向量数据库或大型分词模型。
- 本变更不负责小节/章节扩展和最终 AI 生成摘要。
- 本变更不重写 `pdf_convert.py`、`chunk.py` 或 v2 索引模式。

## Decisions

1. **查询拆分采用标准库优先的混合策略。** 保留原始词（用于英文/空格查询），对连续中文生成二字和三字滑窗，并保留较长的中文候选词。这样不依赖 `jieba`，同时避免单一分词器漏召回。
2. **标准号和条款号走 SQL 精确通道。** 从查询中提取并规范化 `GB55021-2021`、`GB 55021-2021`、`5.3.2`、`第5.3.2条`，直接匹配已存在的 `standard_number`、`clause_number` 字段；精确通道优先级高于普通词法通道。
3. **多通道使用加权 RRF。** FTS/BM25 负责主体相关性，LIKE 和字符窗口负责兜底，精确元数据负责高置信定位。融合不改变 scope，也不让 context-only parent 进入竞争。
4. **诊断信息写入 `ChunkRecord.metadata`。** 记录 `retrieval_channels`、`matched_terms`、`fusion_score` 和原始分数，便于后续 rerank 和评测。
5. **兼容异常旧 schema。** v2 查询先检查字段可用性；legacy 适配器保持原有行为，仅复用改进后的查询 token 生成，不依赖 v2 字段。

## Risks / Trade-offs

- 字符 n-gram 会增加候选噪声和 SQL 次数 → 限制窗口数量、候选池大小，并用 RRF 与精确命中加权。
- 中文滑窗可能把常见短词排到前面 → 保留完整关键词和 BM25，字符通道只作为低权重补充。
- 标准号格式存在 OCR 空格/连字符差异 → 同时生成紧凑和带连字符形式，并保留普通文本通道兜底。
- 旧数据库缺少 v2 列 → 在 v2 与 legacy 路径中分别使用可用字段，不改变旧表结构。

## Migration Plan

1. 仅部署 `retrieve.py` 和测试变更，不需要重建现有 v2 索引。
2. 先运行现有测试，再运行中文自然问题回归集。
3. 若融合排序出现噪声，可通过配置常量降低字符通道权重；回滚只需恢复旧 `retrieve.py`。

## Open Questions

- 后续 embedding 召回是否与本确定性通道采用统一 RRF 参数，需要单独评测集决定。
- 小节/章节上下文扩展应在 `retrieve.py` 返回分组，还是由后续 `rerank.py`/`generate.py` 负责，需要另一个变更讨论。
