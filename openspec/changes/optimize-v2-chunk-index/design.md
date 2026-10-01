## Context

`pdf_convert.py` 和 `chunk.py` 已形成 `knowledge-conversion.v2`/`knowledge-chunks.v2` 两级结构：一个文档包含页面、块、表格、图片以及 parent/child chunks。现有索引只保存七个扁平字段并把每条记录都写入同一 FTS5，无法利用新版切片的结构信息。约束是保持 `ChunkRecord` 外层 JSON 兼容、保留 `[KB:document_id:location]` provenance、避免新重量级依赖，并允许旧数据库继续被 GUI 使用。

## Goals / Non-Goals

**Goals:**

- 建立可迁移、可检查的 v2 SQLite schema；完整保存 chunk metadata 和页面级证据。
- 让 child retrieval chunk 进入加权 FTS5，parent 作为可补全上下文而非普通 top-k 噪声。
- 支持表格行、图片 OCR、标准号、条款号和章节标题的精确/高权重匹配。
- 支持单文档幂等更新、源 hash/schema 变化检测、批量事务和 manifest 统计。

**Non-Goals:**

- 本变更不重写 PDF 转换、结构切片或 OCR 逻辑。
- 不在本轮引入 embedding、向量数据库、查询改写或新的 reranker。
- 不覆盖历史 `knowledge_base.sqlite3`，也不删除低质量证据。

## Decisions

1. **关系表 + JSON 双存储。** 常用筛选字段（role/type/clause/page/quality）单列存储并建立索引，完整 metadata_json 原样保留，兼顾查询性能和可追溯性。
2. **FTS 只收 retrieval child。** 使用 `text_search` 作为正文列，额外写入 heading/clause/standard/table/image 列，并通过 `bm25` 列权重提高结构命中；parent 通过关系表按需补全。
3. **稳定 hash 与幂等重建。** `content_hash=sha1(normalized text)`，`source_sha256 + chunk schema + index schema` 作为文档重建依据；同一文档更新在独立事务中先清理后写入，失败自动回滚。
4. **表格和图片保留专项事实。** 从 chunk metadata 提取 `table_id`/`image_id` 写入专项表，表格行和图片 OCR 仍可检索，`needs_review` 只影响排序/标记，不直接删除。
5. **兼容旧接口。** `build_index(payload, db_path)` 签名保持不变；retrieve 在检测新 schema 时使用新字段，旧库继续走现有 legacy adapter。

## Risks / Trade-offs

- [Risk] FTS schema 变更会使旧 pipeline 数据库无法直接复用 → 使用 `pipeline_*_v2` 表和 manifest 版本检测，必要时输出新数据库文件。
- [Risk] parent 补全增加响应长度 → 仅补全命中 child 的首个 parent，并受 `max_chars` 限制。
- [Risk] 结构字段抽取依赖切片元数据质量 → 保留原 metadata 和质量标记，缺失字段使用空值而不阻断索引。
- [Risk] SQLite FTS5 在极大语料上的写入耗时 → 每文档单事务、WAL、批量 executemany，并在导入后执行统计。

## Migration Plan

1. 新建/升级 v2 pipeline 表并写入当前 `knowledge-chunks.v2` JSON。
2. 用 manifest 校验 chunk/schema/source hash；未变化文档跳过写入。
3. 通过新 retrieve 路径做召回对比，确认 provenance、scope 和 parent 补全后再切换调用方数据库路径。
4. 回滚时继续使用旧 `knowledge_base.sqlite3` 和 legacy adapter；不删除任何旧表。

## Open Questions

- 生产环境是否在索引稳定后外挂本地 embedding；本变更暂不决定模型。
- parent 补全的默认数量是否需要由 GUI/生成阶段配置；当前采用每个命中 child 一个 parent 的保守策略。
