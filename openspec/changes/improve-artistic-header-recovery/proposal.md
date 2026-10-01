## Why

当前 Docling 能够稳定提取页面正文和版面结构，但对部分中文艺术字页眉会出现单字误识别，例如将“建材发展导向”识别为“建材发展导白”。由于页面整体正文充足，现有按页面触发 OCR 的策略不会复核这类局部错误，导致错误文本进入切片、索引和检索链路。需要在保留 Docling 结构化结果的前提下，对可定位的页眉块进行低成本本地复核，并保留可审计的原始值。

## What Changes

- 为 PDF 转换增加默认开启的艺术字页眉局部复核配置。
- 仅对 Docling 标记为 `page_header` 且具有边界框的块执行页面级 RapidOCR 复核，不对整份文档强制 OCR。
- 将 OCR 像素坐标映射为 PDF 坐标，并按位置将识别结果匹配到页眉块。
- 对满足置信度、位置重叠和文本合理性条件的结果更新显示文本、检索文本和结构化文本。
- 在块元数据中保留原始文本、OCR 文本、置信度、处理状态和方法，区分已恢复、已确认和需复核。
- 在质量指标中报告页眉复核、恢复和需复核数量。
- 保持现有 Docling/PyMuPDF 主路径、下游切片/索引接口和输出格式兼容。

## Capabilities

### New Capabilities

- `artistic-header-recovery`: 对结构化 PDF 页眉中的局部艺术字进行本地 OCR 复核、保守替换和审计。

### Modified Capabilities

无。

## Impact

- 主要影响 `knowledge_pipeline/pdf_convert.py` 的页面转换和质量报告逻辑。
- 复用现有 RapidOCR 适配器和页面渲染函数，不新增远程服务或必需依赖。
- 转换结果中的页眉文本可能从 Docling 原始值修正为高置信度局部 OCR 值；原始值通过元数据保留。
- 测试输出继续写入 `knowledge_pipeline/test/results`，不改动生产知识库、数据库或活动 manifest。
