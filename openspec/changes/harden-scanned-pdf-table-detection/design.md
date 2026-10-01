## Context

当前 `_preflight_page()` 以 `len(drawings) >= 4` 判断页面具有表格，`_table_candidates()` 随后生成整页 bbox。扫描论文中的页面边框、图像边缘、坐标轴和分栏线因此被视为表格；整页候选又扩大了可参与单元格匹配的范围。与此同时，Docling 布局文本会覆盖 PyMuPDF 原生文本计数，使纯图片页最终被报告成 `native`。

## Root Cause

1. 表格候选信号只有绘图数量，没有网格拓扑和图片占比约束。
2. 候选 bbox 使用整页矩形，缺少局部区域检测。
3. Docling 布局文本和 PDF 原生文本共用 `native_text/native_chars` 变量，来源丢失。
4. 扫描占主导文档仍启用 PDF cell matching，孤立 OCR/PDF cell 会进入最近列回填。
5. 质量评分未纳入表格有效率和需复核比例。

## Design

### Source-aware page classification

- 保留 `preflight.native_chars` 作为唯一 PDF 原生文本计数。
- 单独记录 `docling_text_chars`。
- 当原生文本不足、Docling 有效文本充足且页面含大面积图像时，分类为 `scanned`，提取方式记为 `layout:docling+ocr-inferred`。

### Scan-dominant Docling configuration

- 在 Docling 调用前统计预检中扫描页面占比。
- 扫描页占比达到 80% 时，将 `table_structure_options.do_cell_matching` 设为 `False`。
- 对带有重复高密度页面绘图模板的转换型/扫描型文档，即使 PDF 暴露可见文字层，也将其视为 cell-matching 高风险文档并关闭该选项。
- 原生或混合文档继续使用 Docling 默认 cell matching，避免改变其原生 PDF 表格行为。

### Vector grid detection

- 从 `page.get_drawings()` 提取轴对齐水平线、垂直线和矩形边。
- 只保留由至少 2 条水平线、2 条垂直线和 4 个交点组成的连通网格。
- 为每个连通网格生成局部 bbox；拒绝面积过小或覆盖接近整页的区域。
- 原生文本不足且图片覆盖率高的页面直接跳过此候选路径。

### Deduplication and optional backends

- 结构化来源优先级保持 Docling > Camelot > pdfplumber > vector candidate。
- 无 HTML 的 vector candidate 与已有结构化表格区域显著重叠时，作为 alternative source 记录并从顶层表格列表移除。
- 候选页集合为空时，`_try_optional_tables()` 立即返回，不导入可选后端。

### Quality metrics

- 输出结构化表格数、候选数、有效 HTML 数、需复核数、无效表格数、有效率和复核率。
- `quality_score` 在页面质量之外，根据无效表格比例和需复核比例扣分。
- `needs_review` 由实际低质量页、失败页、页眉复核或表格质量问题触发，不再因“存在任意表格”直接触发。

## Compatibility

- 新字段为附加字段，现有消费者可继续读取原字段。
- 候选记录仍保留在转换结果中用于诊断，但不会伪装成结构化表格。
- 不引入远程服务，不修改源 PDF。
