# Design: 知识管线测试启动脚本

## 目录与固定路径

脚本位于 `knowledge_pipeline/test/`，统一将结果写入其下的 `results/`。每个脚本文件顶部必须直接声明完整的输入和输出路径，不能只通过共享辅助模块间接暴露路径。输入 PDF 固定为项目已有的 `knowledge_base/source_files/GB 55034-2022 建筑与市政施工现场安全卫生与职业健康通用规范.pdf`。SQLite、嵌入 sidecar 及 manifest 均为测试副本，不引用或覆盖活动库。

## 调用边界

每个启动脚本只负责参数准备、计时、调用现有模块入口和摘要输出：

- `test_pdf_convert.py` 调用 `pdf_convert.main`，使用当前 v3 CLI 参数。
- `test_chunk.py` 调用 `chunk.main`。
- `test_index.py` 调用 `index.main`。
- `test_embed.py` 调用 `embed.main`，使用当前默认本地模型配置。
- `test_retrieve.py` 调用 `retrieve.main`，默认执行确定性词法/混合检索，不传 semantic sidecar。
- `test_semantic_retrieve.py` 调用 `semantic_retrieve.main`，读取测试 sidecar；缺少模型或 sidecar 时明确失败。
- `test_rerank.py` 调用 `rerank.main`。
- `test_generate.py` 调用 `generate.main`。
- `test_rag_pipeline.py` 按依赖顺序调用上述现有入口，验证全链路输出。

脚本通过项目根目录加入 `sys.path`，所以从任意当前目录启动都能导入包。所有模块异常都保留原始类型和消息，并以非零状态结束。

## 摘要与验证

脚本在调用后读取生成 JSON/SQLite 的只读统计，打印输入、输出、耗时、文件大小和模块特有计数（页数、chunk 数、索引行数、召回数等）。这些统计不改变模块输出格式，也不代替人工质量复核。

## 失败策略

语义嵌入依赖外部模型，`test_embed.py` 和 `test_semantic_retrieve.py` 不伪造成功；依赖缺失时打印安装/模型提示并返回非零状态。端到端脚本默认不包含语义步骤，以便在无模型环境中验证确定性主链路。
