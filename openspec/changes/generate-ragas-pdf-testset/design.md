## Context

测试目录包含三份 PDF：200 页扫描规范、85 页扫描论文和 3 页文本文章。当前 `pipeline.sqlite3` 已包含它们的结构化 retrieval chunks，数量分别为 759、329 和 14。RAGAS Conda 环境安装了 RAGAS 0.4.3、LangChain 和 datasets，但没有 PDF/OCR、Transformers 或 sentence-transformers。因此重复从 PDF 原文件解析既昂贵又会丢失项目现有页码、表格、OCR 和 provenance。

## Goals / Non-Goals

**Goals:**

- 使用 RAGAS 官方 `TestsetGenerator.generate_with_langchain_docs` 生成候选测试集。
- 将每个 RAGAS 输入节点绑定到原项目 document/chunk/page/source marker。
- 对三份输入文档做分层覆盖，生成 150 条候选并保留 RAGAS 原始输出。
- 提供项目审核格式、manifest、coverage report 和可复现命令。
- 所有自动结果保持非 Gold 状态。

**Non-Goals:**

- 不重新运行 PDF OCR/转换，不修改现有解析结果和数据库。
- 不将自动生成答案视为人工确认事实。
- 不覆盖 formal_v1 Gold 或 evaluation_v2。
- 不输出、复制或持久化任何 API key。

## Decisions

1. **从现有 SQLite retrieval chunks 构建文档。** SQLite 已覆盖三份源 PDF，并保存 `chunk_id`、页码、heading、table/OCR 类型和 `source_marker`。相比直接用 RAGAS PDF loader，此方法保留工程解析质量和引用链。
2. **按文档分层生成。** 为防止 200 页规范垄断测试集，保持原有 3:2:1 比例，目标配额为规范 75、论文 50、短文 25，共 150 条；每份文档单独调用 generator，失败可独立重试和记录。
3. **合并相邻 chunks 为 RAGAS 文档单元。** 每个单元控制字符预算，保留组成 chunk IDs/pages，并避免把不同源文档混合。这样减少知识图谱节点数和远程调用量，同时保留回溯依据。
4. **远程 LLM、本地确定性 embedding。** RAGAS 合成问题必须使用 LLM；复用 GUI 配置中的兼容 Responses 模型。Embedding 使用 512 维字符 2/3-gram 哈希向量，满足 RAGAS 相似性/聚类需求且不引入远端 embedding API。该向量不是生产 BGE 语义索引，manifest 必须明确区分。
5. **双格式输出。** 保存 RAGAS 原始 `to_list()` JSONL；另生成项目候选 JSONL，包含自动问题、参考答案、reference contexts、绑定的 chunk/page/source、`needs_human_review` 和空白 review 字段。
6. **安全配置读取。** 脚本从现有 GUI 配置读取 endpoint/model/key 供运行时调用，但 manifest 只记录 endpoint 类别、model 和 `api_key_present`，不记录 key。

## Risks / Trade-offs

- [兼容端点不支持 RAGAS 所需结构化调用] → 先运行小规模 smoke test；按文档保存错误，不生成伪样本。
- [字符哈希 embedding 语义质量弱于 BGE] → 只用于 RAGAS 图构建/聚类，并在 manifest 中声明；生成问题仍由 LLM 基于原文。
- [扫描 OCR 错误传播到答案] → 所有记录强制 `needs_human_review`，保留原页和 source marker。
- [短文内容不足以生成 25 条不重复问题] → 允许实际数量少于目标并在 coverage report 报告，不复制问题填充。
- [生成成本和时间较高] → 合并 chunks、限制总数、使用 RAGAS cache/重试和分文档输出。

## Migration Plan

1. 先验证 RAGAS 环境、输入文档映射和小规模生成。
2. 运行三份文档的分层生成到新的 `ragas_pdf_v1` 目录。
3. 校验所有记录为非 Gold、来源可回溯、输入哈希一致。
4. 人工审核副本后才允许另行晋级，现有 formal_v1 不受影响。
