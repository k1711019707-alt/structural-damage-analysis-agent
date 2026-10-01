## Why

现有检索已具备 FTS5/BM25、标准号/条款号精确召回和中文字符级兜底，但对用户措辞与规范原文差异较大的问题仍可能漏召回。需要增加本地中文 embedding 旁路通道，在不替换可解释词法证据的前提下补充语义候选，为后续小节上下文扩展和 AI 总结提供更完整的锚点。

## What Changes

- 新增本地 embedding 构建脚本，面向 v2 retrieval child 生成并持久化归一化向量。
- 新增语义召回脚本，支持作用域过滤、top-k、模型/索引元数据校验和离线运行。
- 扩展 `retrieve.py` 以可选方式融合 semantic、FTS、LIKE、标准号和条款号通道。
- 语义模型或向量索引不可用时自动回退到现有确定性召回，并暴露回退原因。
- 保留 parent hydration、质量标记、来源引用、legacy 适配器和现有 SQLite v2 索引。

## Capabilities

### New Capabilities
- `local-semantic-retrieval`: 本地中文 embedding 索引、语义召回和混合融合能力。

### Modified Capabilities

## Impact

新增 `knowledge_pipeline/embed.py` 和 `knowledge_pipeline/semantic_retrieve.py`，修改 `knowledge_pipeline/retrieve.py`、测试和依赖文档。第一版默认使用可配置的 `BAAI/bge-small-zh-v1.5`，模型由运行环境提供，不把权重提交到仓库；不要求引入 FAISS，千级/万级 child 先用 NumPy 旁路矩阵。
