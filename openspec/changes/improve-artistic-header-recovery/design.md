## Context

Docling 负责当前 PDF 的结构化版面解析。页眉艺术字通常没有可用的 PyMuPDF 原生文本层，但可从页面渲染图像中由现有 RapidOCR 识别。页面级 OCR 触发条件不能发现正文充足页面中的局部页眉错误，因此需要一个只覆盖页眉候选块的复核阶段。

## Goals / Non-Goals

### Goals

- 在不改变正文、表格、图片和公式主路径的情况下纠正高置信度页眉艺术字。
- 页面每次最多初始化/调用一次 OCR，控制额外耗时。
- 让每次替换都可追溯，并使低置信度结果停留在 `needs_review` 而不是静默覆盖。
- 使修正后的文本自然进入现有 chunk、index、retrieve 链路。

### Non-Goals

- 不把局部复核扩展为全文 OCR。
- 不引入远程视觉模型或网络调用。
- 不对普通正文、表格或非页眉 Docling 块作自动改写。
- 不以跨页多数票替代局部视觉证据。

## Proposed Design

1. 在 `PdfConversionOptions` 增加 `review_artistic_headers=True`、`header_review_scale=3.0`、`header_review_confidence=0.88` 等本地配置。
2. 每页完成 Docling 块映射后，筛选 `metadata.docling_label == "page_header"` 且有 `bbox` 的块。
3. 对有候选的页面调用 `_render_page` 和 `_structured_ocr_result(_ocr_adapter(), image)` 一次。
4. 将 RapidOCR 返回的像素边界框转换为 PDF 坐标；以 IoU/中心距离/包含关系筛选与页眉块的最佳匹配。
5. 只在 OCR 文本非空、置信度达到阈值、字符内容合理且与原块不是纯格式差异时更新 `text`、`text_display` 和 `text_search`。
6. 在 `metadata.header_review` 写入 `status`、`original_text`、`ocr_text`、`ocr_confidence`、`source` 和位置匹配信息；未达到替换条件但存在候选时记录 `needs_review`。
7. 将页面级计数汇总到转换质量指标，字段至少包含复核总数、恢复数、确认数和需复核数。

## Data Flow

```text
Docling page blocks
        |
        v
page_header candidates --none--> existing conversion
        |
        v
render page -> RapidOCR -> PDF bbox mapping
        |
        v
conservative match + confidence gate
        |
        +--> update text projections + audit metadata
        +--> preserve original on needs_review
        v
chunk -> index -> retrieve (unchanged contracts)
```

## Error Handling and Observability

- 页面渲染、OCR 初始化或 OCR 结果解析失败时，不影响主转换；记录非致命 warning，并保留 Docling 文本。
- 低置信度、位置不匹配或文本不合理时不替换，状态为 `needs_review`。
- 质量报告明确记录复核统计，避免把局部 OCR 误报为整页 OCR 成功。

## Compatibility

- 默认行为仅新增页眉局部复核；关闭 `review_artistic_headers` 时恢复既有结果。
- `DocumentConversion`、块字段和下游输入保持兼容；仅新增可选 metadata/quality 字段。
- 所有路径继续使用现有项目解释器和测试输出目录。
