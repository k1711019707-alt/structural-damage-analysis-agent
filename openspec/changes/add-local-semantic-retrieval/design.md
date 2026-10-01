## Context

v2 知识管线已经通过 `pdf_convert.py`、`chunk.py` 和 `index.py` 产生结构化 child/parent、标题、条款、标准号、表格和图片 OCR 字段。当前 `retrieve.py` 的确定性多通道召回已修复连续中文问题，但无法覆盖同义改写和跨表述语义匹配。当前环境已有 PyTorch、NumPy 和 ONNX Runtime，但尚未安装 `sentence-transformers`、FlagEmbedding 或 FAISS。

## Goals / Non-Goals

**Goals:**

- 为 retrieval child 建立可复现、可校验、可增量更新的本地向量旁路索引。
- 使用本地中文 embedding 对查询召回语义候选，并与现有词法/精确通道融合。
- 维持严格 document scope、child-only ranking、parent hydration、来源和质量元数据。
- 在模型、权重、索引或依赖不可用时安全回退，不阻塞现有检索。

**Non-Goals:**

- 本变更不实现小节/章节扩展和 AI 摘要生成。
- 本变更不替换 FTS5，不引入远程 API、向量数据库或 FAISS 强依赖。
- 本变更不提交模型权重，不改变 PDF 转换、切片和 v2 关系模式。

## Decisions

1. **采用独立旁路向量文件。** `embed.py` 输出 NumPy `.npz` 或等价 JSON 元数据，包含 chunk_id、content_hash、model_name、dimension、normalized 和矩阵；避免修改现有 SQLite schema，便于回滚和重建。
2. **模型适配器可选加载。** 优先加载 `sentence-transformers` 的 BGE 模型；未安装或加载失败时返回结构化 unavailable 状态。后续可增加 ONNX 适配器而不改变 semantic retriever 接口。
3. **只向量化 retrieval child。** parent/context_only 不参与语义 top-k，保持与 FTS 一致；查询结果通过 `chunk_id` 回到 SQLite 获取完整元数据。
4. **余弦相似度使用归一化矩阵点积。** 对当前规模足够快、依赖少；规模扩大后可以替换为 FAISS/HNSW 但保持相同索引契约。
5. **混合融合采用现有 RRF 框架。** semantic 候选记录 `semantic_score` 和 rank，和 FTS、LIKE、exact 通道按可配置权重融合；没有 semantic 候选时 retrieval mode 保持确定性模式并记录 fallback reason。
6. **索引一致性以 content_hash 校验。** 查询时忽略或降权向量索引中已不在 SQLite、角色不是 retrieval 或 content_hash 不匹配的条目，避免旧向量污染新切片。

## Risks / Trade-offs

- BGE 模型下载和首次加载耗时 → 模型路径可配置，构建/查询前做健康检查并缓存模型。
- 桌面端内存和推理速度受模型影响 → 默认 small 模型、批处理和 CPU 兼容；语义通道可关闭。
- 语义召回可能带来主题相近但规范不精确的结果 → FTS/标准号/条款号始终保留并提高精确通道权重。
- 向量索引与 SQLite 不一致 → 保存 source/index fingerprint、content_hash，查询时严格过滤并提示重建。

## Migration Plan

1. 在不改旧数据库的情况下，用 `embed.py` 从现有 v2 SQLite 生成旁路向量索引。
2. 以 `semantic_retrieve.py` 独立验证 top-k 和 scope，再开启 `retrieve.py` 的可选 semantic retriever。
3. 对比词法基线与混合召回评测集；异常时关闭 semantic 通道或删除旁路索引即可回退。
4. 未来模型升级通过新索引目录并行构建，验证后切换 manifest，不覆盖旧索引。

## Open Questions

- 真实知识库规模超过十万 child 后是否迁移 FAISS/HNSW，需要基准测试决定。
- BGE-small 与 BGE-m3 的召回/延迟/内存权衡，需要使用工程问题评测集验证。
- 章节扩展应使用 semantic anchor 的 heading_path 聚合还是额外结构索引，需要后续变更确定。
