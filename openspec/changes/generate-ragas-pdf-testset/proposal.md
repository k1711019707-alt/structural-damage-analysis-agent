## Why

项目已有 150 题人工核对 Gold，但用户希望使用本机 RAGAS 0.4.3 从 `knowledge_pipeline/test/测试文档` 的三份 PDF 重新生成一套独立测试集候选，以扩展问题覆盖并为后续 RAGAS 生成质量评估提供标准输入。

## What Changes

- 新增固定使用 Conda `RAGAS` 环境的候选测试集生成脚本。
- 复用项目已经完成的 PDF 解析、OCR 和结构化 chunk，不在 RAGAS 环境中重复解析扫描 PDF。
- 将测试目录中的三份 PDF 对应 retrieval chunks 转成保留页码、chunk ID、document ID 和来源标记的 LangChain 文档。
- 使用 RAGAS 0.4.3 `TestsetGenerator` 生成分层的单跳候选问题及参考答案，默认目标为 150 条（规范 75、论文 50、短文 25），输出 RAGAS 原始格式和项目审核格式。
- 所有自动生成记录标记为 `needs_human_review`、`gold_label=false`，不覆盖现有 formal_v1 Gold、SQLite、语义索引或原 PDF。
- 输出输入文件哈希、RAGAS/模型配置、文档覆盖、失败项、可复现命令和人工审核模板。

## Capabilities

### New Capabilities

- `ragas-pdf-testset-generation`: 从项目已有 PDF 解析 chunks 构建可审计的 RAGAS 候选测试集。

### Modified Capabilities

- 无。

## Impact

- 新增 `scripts/generate_ragas_pdf_testset.py`、对应测试及 `benchmarks/rag/ragas_pdf_v1/` 输出。
- RAGAS 生成调用固定使用 `D:\anaconda\envs\RAGAS\python.exe` 和 RAGAS 0.4.3；项目解析数据只读。
- 生成模型复用 GUI Responses 配置但不记录或输出密钥；Embedding 使用脚本内确定性中文字符 n-gram 适配器，不要求远端 embeddings 服务。
