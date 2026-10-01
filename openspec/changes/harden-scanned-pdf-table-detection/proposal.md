## Why

扫描型 PDF 当前会因简单的“绘图数量不少于 4”规则被大面积误判为表格页面，并生成覆盖整页的候选框；同时 Docling 在扫描型文档中尝试将 OCR 单元格回填到 PDF 表格网格，产生大量 `Orphan pdf_cell` 告警。结果虽然能够完成转换，但页面类型、表格数量和 `quality_score` 不能准确反映实际质量。

## What Changes

- 根据原生文本、图片覆盖率和文档扫描页占比区分原生文字页与 Docling OCR 扫描页。
- 对扫描占主导的文档关闭 Docling 的 PDF cell matching，保留表格模型输出并避免不可靠的最近列回填。
- 用水平线、垂直线、交点和局部网格区域替代单纯的绘图数量表格候选规则。
- 在原生文本不足且图片覆盖率高的页面禁用矢量表格候选。
- 将矢量候选限制为局部 bbox，并与 Docling/Camelot/pdfplumber 的结构化表格按区域去重。
- 扩充页面来源和表格质量指标，并将无有效 HTML、需复核表格比例纳入质量评分。
- 当没有符合条件的候选页时，不加载或调用 Camelot/pdfplumber，减少无关的 `pypdf`/`MediaBox` 日志。

## Capabilities

### New Capabilities

- `scanned-pdf-table-quality`: 扫描型 PDF 的来源分类、表格候选准入、结构化表格去重和质量计分。

### Modified Capabilities

无。

## Impact

- 修改 `knowledge_pipeline/pdf_convert.py`、共享页面/质量契约和对应测试。
- 保留 Docling-first 主路径、现有 JSON schema 标识和下游 chunk/index/retrieve 接口。
- 纯图片文档的 `native_pages`、`ocr_pages`、表格候选数和质量分会变得更严格、更符合来源事实。
- 不覆盖或重写原始 PDF，不修改生产知识库、生产数据库或活动 manifest。
