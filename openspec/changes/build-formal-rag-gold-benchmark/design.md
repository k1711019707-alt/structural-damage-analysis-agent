## Context

现有 `benchmarks/rag/retrieval_qa_benchmark.jsonl` 由旧生产库自动生成，只有 21 题，且当前旧生产库已被最新索引契约判定为 `schema_incompatible`。最新完整流水线结果位于 `knowledge_pipeline/test/results/`：SQLite 索引、切片、解析和检索产物可相互追溯，但尚无固定配额的正式问答集。

基准的主要使用者是检索/重排/生成模块开发者和人工标注者。工程规范中的数字、单位、否定和例外条件属于高风险信息，不能把当前系统自己的 top-k 输出直接循环标成 Gold。

## Goals / Non-Goals

**Goals:**

- 从显式指定的最新测试 SQLite 快照生成恰好 150 条、八类配额固定的正式候选记录。
- 正样本从真实 retrieval chunk 中取证，并保留完整 chunk、文档、页码、来源标记和数据库哈希。
- 自动生成候选与人工复核副本分离；只有满足发布校验的人工记录才能晋级 Gold。
- 提供 JSONL、CSV、Markdown、manifest 和统计报告，支持后续计算 Hit/Recall/MRR/nDCG、范围泄漏和无答案指标。
- 生成过程可复现、确定性、只读，不依赖在线模型。

**Non-Goals:**

- 不声称自动候选答案已经获得人工原 PDF 核验。
- 不修改索引、检索、重排或生成模块，不重建生产库，不迁移旧 21 题文件。
- 不用自动检索结果直接定义相关性真值，不自动将记录标为 `gold_reviewed`。
- 本变更不完成 150 题的双人专家标注；它交付正式候选集、审核包和发布门禁。

## Decisions

1. **绑定最新测试快照而非旧生产库。** 默认输入为 `knowledge_pipeline/test/results/index/pipeline.sqlite3`，因为旧生产库与当前 `index.v2.2` 契约不兼容。命令行允许显式覆盖路径，但 manifest 必须记录 SHA-256。
2. **以 retrieval child 为直接证据单位。** 父 chunk 和层级扩展只可作为支持上下文，不可自动进入 `manual_relevant_chunk_ids`。这样避免把检索系统自身的扩展行为变成相关性真值。
3. **采用确定性分层采样。** 先按表格、数值/单位、条款号、跨页、否定词、文档和页码等元数据建立候选池，再按固定随机种子选择，限制同页/同 chunk 重复，保证配额与可复现性。
4. **答案是“复核候选”，Gold 是发布状态。** 自动记录包含 `candidate_answer`、`required_facts` 和 `forbidden_errors`，但固定为 `annotation_status: needs_human_review`、`gold_label: false`。人工在独立 review JSONL 中补充 `gold_answer`、直接/支持证据标签和核验人后，发布校验器才允许生成 `gold.jsonl`。
5. **无答案题使用库外或越界问题模板。** 这些记录的相关证据集合必须为空，并需人工确认当前限定语料确实不含答案；检索返回非空不等于有答案。
6. **原始数据与展示数据并存。** JSONL 保存完整结构，CSV 便于表格标注，Markdown 提供抽样浏览，manifest/coverage_report 提供版本、哈希和分布证明。

## Risks / Trade-offs

- [自动问题可能生硬或答案截断] → 提供独立人工复核字段和发布门禁，自动产物不标 Gold。
- [单一 200 页扫描规范不能覆盖全部工程领域] → 基准记录文档分布；后续可在同一 schema 下加入更多已索引文档版本。
- [索引证据可能继承 OCR 错误] → 标注者必须对照原 PDF 页；数字/单位、表格、否定/例外题列为重点复核。
- [无答案题可能在其他项目 PDF 中有答案] → 无答案语义限定为本次 manifest 中的 scope 文档，而不是整个工程知识领域。
- [同一 chunk 可派生多个题型导致泄漏] → 生成器限制主要证据复用，并在报告中输出重复证据率；正式评测拆分时按 evidence group 分组。

## Migration Plan

1. 在独立 `benchmarks/rag/formal_v1/` 目录生成候选，不覆盖现有 `benchmarks/rag/*.jsonl`。
2. 运行结构和配额校验，固化输入数据库及源文件哈希。
3. 人工复制/填写 review 文件并逐页核对原 PDF。
4. 仅在发布校验通过后输出 Gold 文件；若审核未完成，保留候选状态。
5. 回滚只需删除独立版本目录和新增脚本；生产库和现有 benchmark 不受影响。

## Open Questions

- 本次交付先提供单人复核字段；对外正式发布前是否要求双人独立标注和冲突仲裁，由项目验收流程决定。
