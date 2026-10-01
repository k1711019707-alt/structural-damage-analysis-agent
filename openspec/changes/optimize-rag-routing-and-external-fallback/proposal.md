## Why

150 题正式 Gold Set 的首轮消融表明，现有统一 RRF 候选池会挤出有效词法候选，来源标题通道会干扰 scope 内正文排序，无条件层级扩展增加延迟但没有改善 direct 指标；同时，知识库无证据问题只能报告不足，不能按用户要求安全转入联网搜索或模型一般知识。

## What Changes

- 新增不依赖 Gold 标签的查询类型分类与检索路由，针对标准号/条款、表格、数字单位、风险条件、自然语言语义改写和普通查询动态选择通道权重与候选配额。
- 将混合候选合并改为保留式 union：保留词法、语义和精确匹配候选后再融合/重排，避免混合 Hit@30 低于词法基线。
- 将来源标题从强 chunk 排序信号降为文档路由/弱先验；显式 scope 下不再给该文档全部 chunk 大额加分。
- 新增按查询复杂度、anchor 置信度和上下文预算执行的层级上下文门控，避免简单单点问题无条件扩展。
- 新增 `knowledge_base`、`web_search`、`model_prior` 三态回答来源合同：知识库无答案时优先真实联网搜索，不可用时允许模型一般知识，但必须显式标注非知识库证据、时效性风险和人工复核要求。
- 新增 evaluation_v2 评测协议和只读消融运行，报告检索质量、外部回退路由、来源标注、scope 泄漏、上下文覆盖及延迟，不覆盖 Gold、evaluation_v1、生产数据库或活动 manifest。

## Capabilities

### New Capabilities

- `adaptive-rag-routing`: 查询类型路由、保留式混合候选、标题弱化和层级上下文门控。
- `external-answer-fallback`: 知识库无答案时的联网搜索/模型一般知识回退合同及证据边界。
- `rag-evaluation-v2`: 基于既有 150 题 Gold 的新消融配置、外部路由指标和可复现结果包。

### Modified Capabilities

- 无。

## Impact

- 影响 `knowledge_pipeline/retrieve.py`、`knowledge_pipeline/generate.py`、`runtime/knowledge_base.py`、相关生成服务/上下文模型、RAG 评测脚本和测试。
- 不新增必需在线依赖；真实 Web Search 仅在当前 provider 明确支持并返回可核验来源时启用，否则使用 `model_prior` 合同。
- 保持现有 `retrieve()` 和运行时调用方兼容；新增字段提供安全默认值。
