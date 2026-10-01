## Context

当前 PDF 阶段在一次文档级 Docling 调用后逐页补充 PyMuPDF/OCR，表格使用 `rows/markdown`，页面契约缺少图像占比、文本密度和布局复杂度。目标方案要求页面级路由、HTML 表格、原文/OCR/视觉分层和机器可消费 warning，同时保持确定性路径可独立完成。

## Decisions

1. 先执行 PyMuPDF 预检并为每页记录结构化特征；Docling 仍作为默认基础结构解析器，但不重复插入同页 PyMuPDF 布局块。
2. 页面分类采用兼容值：保留 `native/scanned/mixed/blank_or_unreadable`，并新增方案值 `native_text/native_table/complex_layout/figure_page` 等到 metadata/字段中。
3. 表格主结果使用 HTML；旧 `rows/markdown` 仅用于 v2 读取和内部派生。HTML 生成只允许安全表格标签并进行实体转义。
4. 视觉调用只通过注入适配器或可选配置启用；`pdf_convert.py` 不直接硬编码密钥，配置来源通过现有 SettingsStore 解析，失败写 warning 并继续确定性转换。
5. 视觉结果保存为独立 `visual_regions`/图片字段，明确 `ocr_text`、`vision_raw`、`vision_summary`、`vision_search`，不覆盖原文。
6. 质量评分保持非阻断，但增加表格/视觉诊断统计；状态 `success_with_warnings` 不表示高保真。

## Compatibility

本变更明确采用 `knowledge-conversion.v3` 断裂式契约：`TableRecord` 只保留 `html`，不再提供 `rows/markdown` 正式字段；`DocumentConversion` 直接输出 `visual_regions` 和分层视觉字段。下游 v2 管线不在本变更范围内，后续需单独迁移。

## Verification

覆盖 native text、native table、scanned、mixed、figure/complex 页面、HTML 安全校验、后端冲突去重、视觉适配器失败回退、密钥不落盘和现有 PDF/OCR 测试。
