## Why

`knowledge_pipeline/pdf_convert.py` 当前已经具备 Docling-first、PyMuPDF 页面事实和 OCR 回退，但尚未达到 `PDF_DOCUMENT_PARSING_PLAN.md` 定义的页面级预检、页面分类、结构化质量字段、HTML 表格主结果和可选视觉增强边界。需要把确定性解析能力先收敛到方案规定的契约，并保留 v2 下游兼容性。

## What Changes

- 增加页面级预检特征、页面分类和分类原因，并按页面特征决定 OCR/表格候选路径。
- 增加可选 `PdfVisionOptions` 与视觉适配器边界；默认不因视觉模型缺失阻断确定性转换。
- 统一表格 HTML 生成、白名单校验、规范化和去重；保留旧 `rows/markdown` 读取兼容。
- 分层保存原文、OCR 和视觉字段，增加图片/视觉区域记录的兼容输出。
- 扩展质量报告为可解释的文本、表格、视觉诊断字段；warning 继续不阻断流水线。
- 将共享转换契约直接升级为 `knowledge-conversion.v3`，`TableRecord.html` 成为唯一正式表格正文，新增 `visual_regions` 与分层视觉字段。

## Non-goals

- 不在本变更中修复下游 v2 管线；下游迁移另行处理。
- 不默认调用远程视觉模型，不在仓库或输出文件保存密钥。
- 不把视觉模型推断当作原始 PDF 事实。

## Impact

主要修改 `knowledge_pipeline/pdf_convert.py` 和 `knowledge_pipeline/contracts.py`；补充 PDF 转换单测和基准字段。该变更是 v3 断裂式契约，不保证旧 v2 下游继续运行。
