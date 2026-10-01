# Knowledge Pipeline

知识库阶段脚本集中在此目录，阶段顺序为：

```text
pdf_convert -> chunk -> index -> retrieve -> rerank -> generate
```

每个阶段既可作为 Python 模块调用，也可直接运行：

```powershell
python -m knowledge_pipeline.pdf_convert input.pdf converted.json
python -m knowledge_pipeline.chunk converted.json chunks.json
python -m knowledge_pipeline.index chunks.json knowledge_pipeline.sqlite3 --manifest index_manifest.json
python -m knowledge_pipeline.embed knowledge_pipeline.sqlite3 semantic.npz --model BAAI/bge-small-zh-v1.5
python -m knowledge_pipeline.retrieve "结构裂缝修复" knowledge_pipeline.sqlite3 retrieval.json
python -m knowledge_pipeline.retrieve "结构裂缝如何修复" knowledge_pipeline.sqlite3 retrieval.json --semantic-index semantic.npz
python -m knowledge_pipeline.retrieve "结构裂缝如何修复" knowledge_pipeline.sqlite3 retrieval.json --semantic-index semantic.npz --bm25-weight 1.0 --vector-weight 2.0 --fusion-k 60
python -m knowledge_pipeline.rerank retrieval.json reranked.json
python -m knowledge_pipeline.generate reranked.json generation_context.json --prompt "按证据生成工程草案"
python -m knowledge_pipeline.generate retrieval.json damage_summary_context.json --generation-mode damage-grounded-summary --prompt "总结裂缝损伤的检查重点和后续措施"
```

`chunk` 阶段当前输出 `knowledge-chunks.v2`：按 Docling 布局块、PyMuPDF
原生块、OCR 和投影 fallback 选择主文本，保留页码/块/抽取方式来源，识别
标题和条款上下文，并生成 `context_only` parent 与 `retrieval` child。表格
会去重后生成完整 parent 和重复表头的行级 child；空表格候选及无 OCR 文本
的图片只写入诊断/引用信息，不生成空的可检索文本。旧的 `--size`、
`--overlap` 参数仍可作为 child token 限制和重叠量的兼容别名；新配置可用
`--child-max-tokens`、`--parent-max-tokens`、`--overlap-tokens` 和
`--table-rows-per-child` 指定。

`index` 阶段当前输出 `knowledge-index.v2`：将 child retrieval 块写入加权
FTS5，将 parent 作为 context-only 关系保存；正文搜索、标题、条款号、标准号、
表格和图片 OCR 分列索引，同时保留原始/上下文化文本、页码、提取方式、质量标记
和内容 hash。相同源文件、切片配置和 schema 指纹会幂等跳过；变更文档在单文档
事务中重建。建议使用新的 `knowledge_pipeline.sqlite3` 或
`knowledge_base_v2.sqlite3`，不要覆盖历史 `knowledge_base.sqlite3`。

当前 PDF 转换阶段默认尝试 Docling 版面解析，并由 PyMuPDF 提供页级 inventory、带 bbox 的稳定基础块和失败兜底，RapidOCR 负责扫描页回退；Camelot、pdfplumber 默认自动探测表格。Docling 首次运行可能需要下载版面模型，模型下载失败时自动回退 PyMuPDF 并记录 warning。输出保留 PDF 哈希、页码和 `[KB:document_id:location]` 来源标记。可用 `--no-docling`、`--no-camelot`、`--no-pdfplumber` 关闭增强后端。

表格和版面 warning 现在只作为机器可消费的诊断字段，不阻断自动切片、索引和后续处理；`needs_review` 字段仅保留兼容性，不要求人工审核。`rerank.py` 当前是确定性基线，向量模型或专用 reranker 可以在相同输入输出契约上替换。

可选的 `embed.py` 使用本地 `BAAI/bge-small-zh-v1.5` 为 retrieval child 生成 `knowledge-embeddings.v1` 旁路索引；`semantic_retrieve.py` 使用 NumPy 余弦相似度执行语义召回。语义依赖、模型或索引不可用时，`retrieve.py` 自动保留确定性词法召回并写出 warning，不影响原有流程。

混合召回会分别生成 BM25 与向量候选，再按 `chunk_id` 去重并使用加权 RRF 融合；默认 BM25 权重为 `1.0`、向量权重为 `2.0`、`fusion_k=60`。结果 metadata 保留各通道 rank、原始分数、融合贡献和总分。只有 retrieval child 参与 BM25 和向量候选竞争，context-only parent 在 child 命中后回取。
