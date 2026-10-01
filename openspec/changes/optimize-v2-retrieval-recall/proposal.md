## Why

当前 v2 索引中的中文内容可以被空格关键词召回，但连续中文自然问题经常被 `_tokens()` 当作一个不存在的长 token，导致真实相关结果为 0。标准号和条款号也会因格式拆分或数字过滤而失去精确召回，降低后续语义检索和 AI 总结的证据覆盖率。

## What Changes

- 增加中文自然查询规范化与轻量关键词/字符 n-gram 生成，兼容连续中文和中英文混合查询。
- 增加标准号、条款号的规范化识别与精确字段召回。
- 将 FTS5/BM25、LIKE、精确元数据通道合并去重，并使用可解释的加权 rank fusion 排序。
- 保留作用域过滤、child-only 检索、parent hydration、质量标记和 `scoped_fallback` 语义。
- 为召回结果增加命中通道、匹配词和融合分数诊断元数据。
- 保留旧版知识库适配器，避免破坏现有 GUI 兼容入口。

## Capabilities

### New Capabilities
- `chinese-query-recall`: 连续中文自然问题、标准号和条款号的多通道确定性召回。

### Modified Capabilities

## Impact

主要影响 `knowledge_pipeline/retrieve.py` 及其 v2/legacy 检索测试；不改变 PDF 转换、切片和 v2 索引字段。无需新增大型依赖，使用现有 SQLite FTS5、SQL 索引和 Python 标准库实现。
