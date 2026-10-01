## Context

当前测试快照含 4 份文档、1111 个 retrieval child，以及与其指纹匹配的 512 维语义 sidecar。正式候选集恰好 150 题，其中 140 题可回答、10 题无答案。用户已明确完成逐题人工核对且无误，因此本轮可以将候选答案和候选直接证据固化为用户审核的 Gold 标签。

项目检索模块的“词法”不是裸 BM25，而是 FTS5/BM25、中文 LIKE 回退、标准号/条款号精确通道和来源标题通道的组合。语义通道为 BGE 向量余弦检索；混合使用 RRF；重排为现有确定性 reranker；层级模式在直接 anchor 后加入 parent/同标题上下文。

## Goals / Non-Goals

**Goals:**

- 保留用户人工确认的审计声明，并通过 Gold 发布门禁生成不可与候选混淆的版本目录。
- 所有配置使用相同问题、Gold、scope、候选池大小、K 值和快照哈希。
- 直接证据指标只以 Gold direct chunk 为相关项；扩展 parent/context 不冒充 direct relevance。
- 同时报告无答案和范围隔离，避免只展示正向召回。
- 保存逐题排名、耗时、命中、泄漏和失败原因，允许复核汇总值。

**Non-Goals:**

- 不修改检索或重排算法来迎合基准。
- 不调用远程 LLM，不评估最终自然语言答案的 Faithfulness；本轮评估检索、重排和上下文组装能力。
- 不把旧生产库或旧 21 题候选混入本次结果。
- 不把层级扩展的 parent/context 自动算作 direct Gold 命中。

## Decisions

1. **用户声明作为单人项目负责人审核。** 将本轮消息记录为 `reviewer_id: project-owner-user-attestation`，候选答案晋级为 `gold_answer`，候选 direct chunk 晋级为人工 direct evidence；manifest 明确这是用户声明完成的单人审核，不宣称双人独立标注。
2. **固定候选池 30、报告 K=1/3/5/10。** 所有配置先得到至多 30 个直接候选；重排后仍保留 30 个，指标统一截断。层级配置另外保存上下文覆盖，不改变 direct Gold 定义。
3. **公平使用记录 scope。** 每题都使用 Gold 中的 `scope_document_ids`；scope isolation 题专门计算泄漏率。无答案题 scope 是本次 4 文档全集。
4. **五个真实现有模块配置。** `lexical_structured` 调用 `retrieve(..., semantic_retriever=None, expansion_mode='none')`；`semantic_only` 调用 `SemanticRetriever.search`；`hybrid_rrf` 调用带语义 retriever 的 `retrieve`；后两组在混合结果上调用 `rerank_payload`，最后一组启用 `expansion_mode='auto'`。
5. **无答案预测服从当前系统行为。** 返回 direct candidate 且系统标记为 hit 就视为尝试作答；不人为添加未实现的阈值。这样能够暴露纯语义/混合检索的拒答缺口。
6. **二元相关性指标。** Gold direct chunk gain=1，其他为0；MRR 取首个 direct Gold；nDCG 使用 binary DCG。跨页题多个 direct chunk 均计入 Recall。
7. **时间口径。** 每题记录 wall-clock；P50/P95 基于单次真实调用，包括查询向量编码。首次模型加载单独记录为初始化时间，不混入每题延迟。

## Risks / Trade-offs

- [问题由证据派生，指标可能高于真实用户分布] → 报告中声明这是 corpus-grounded closed-book retrieval benchmark，并按题型展示，不能外推为任意问题准确率。
- [用户核对声明没有逐条电子签名] → 保存统一 reviewer/时间/声明字段，明确单人项目负责人确认，不声称双人标注一致性。
- [语义评测耗时较长] → 模型只初始化一次；不中断单题真实编码，保存进度和逐题输出。
- [层级上下文会返回非 direct chunk] → direct 指标只评 anchors/reranked direct IDs，并另报 context coverage/overhead。
- [无答案语义检索必然返回近邻] → 不掩盖结果，单列拒答指标和失败案例。

## Migration Plan

1. 从候选文件创建带用户审核声明的独立 review JSONL。
2. 运行既有 Gold publication gate，输出 `gold_v1/`。
3. 运行消融评测到新的 `evaluation_v1/`，不覆盖 Gold 或候选。
4. 校验逐题数、配置数、公式重算、输入哈希和报告一致性。
5. 若需要重跑，删除或指定新的 evaluation 输出目录即可；知识库和 Gold 不变。
