# PDF 文档解析模块方案（页面级路由、多模态增强、HTML 表格）

本文档定义知识库 PDF 解析阶段的目标架构和实现约束。方案面向工程规范、检测报告、设计资料和扫描附件等混合类型 PDF，目标是提高文字准确率、复杂表格结构保留能力、图表语义召回率和全链路可追溯性。

本文档只描述 PDF 转换阶段，不改变后续切片、索引、召回、重排和生成阶段的职责边界。

## 1. 核心结论

PDF 解析不采用“所有页面统一交给一个解析器”，而采用：

```text
PDF
  ↓
PyMuPDF 页面级轻量预检
  ↓
页面分类和区域路由
  ↓
Docling 基础结构解析
  ↓
OCR、表格解析器和多模态模型按需增强
  ↓
多源结果融合、去重和质量评估
  ↓
统一 DocumentConversion 结果
```

总体原则：

1. 确定性工具负责保留原始事实。
2. 多模态模型只处理复杂视觉语义和低质量难页，不覆盖原始解析事实。
3. 表格识别结果最终只持久化为 HTML。
4. 原文、OCR 转写、视觉模型解释必须分层保存。
5. 所有 warning 都是机器可消费的诊断，不阻断自动切片、索引和后续流程。
6. 每个结果都必须能够追溯到文档、页码、区域和解析后端。

## 2. 与现有项目的关系

当前 PDF 转换入口为：

```text
YOLO11-seg/knowledge_pipeline/pdf_convert.py
```

当前已存在的后端及职责：

| 后端 | 职责 |
|---|---|
| Docling | 版面感知的文本、标题、列表和基础表格解析 |
| PyMuPDF | PDF 打开、页面事实、原生文字块、bbox、图片、绘图和基础兜底 |
| RapidOCR | 原生文字不足时的扫描页 OCR 回退 |
| Camelot | 原生、有边框表格的结构化增强 |
| pdfplumber | 原生 PDF 表格的另一条增强路径 |
| 多模态大模型 | 复杂图表、复杂表格、OCR 难页和视觉语义增强 |

Docling 和多模态模型不是互相替代的关系：

```text
Docling：负责“把文档结构读出来”
多模态模型：负责“理解复杂视觉内容并补充语义”
```

## 3. 多模态模型配置

### 3.1 配置来源

多模态模型沿用当前 GUI 的 Responses API 配置，不在 PDF 解析脚本、代码仓库或 Markdown 文档中保存密钥明文。

当前 GUI 的统一设置由 `runtime.settings_store.SettingsStore` 读取，优先使用用户配置目录中的：

```text
C:\Users\17110\AppData\Local\YOLO11DamageDesktop\config\gui_settings.json
```

配置字段为：

```text
api.responses_url
api.responses_key
api.responses_model
```

当前检测到的 GUI Responses API URL 为：

```text
https://chat.ai666.net/api/codex
```

密钥字段存在，但本文档不显示、不复制、不硬编码，也不写入转换结果、日志、报告或测试快照。PDF 解析模块应在运行时通过现有设置加载机制取得密钥；密钥缺失时写入 warning 并回退到无多模态增强的确定性路径。

### 3.2 模型固定值

本方案的多模态模型固定为：

```text
gpt-5.5
```

即使 GUI 当前的默认生成模型仍为其他值，PDF 解析阶段也应显式使用：

```text
model = "gpt-5.5"
```

URL 和 key 沿用 GUI，模型单独覆盖为 `gpt-5.5`。建议增加独立的解析配置对象，避免修改损伤报告或施工方案的全局模型设置：

```python
PdfVisionOptions(
    enabled=True,
    base_url=<GUI api.responses_url>,
    api_key=<GUI api.responses_key>,
    model="gpt-5.5",
)
```

### 3.3 调用边界

多模态调用应满足：

- 使用当前 GUI 已验证的 OpenAI-compatible Responses API URL；
- 通过现有 `normalize_responses_base_url()` 规则规范化 URL；
- API key 只存在于内存和请求头，不写入结果文件；
- 异常信息必须脱敏，禁止出现完整 key；
- 每页或每个区域设置超时、重试次数和最大输入尺寸；
- 记录模型名、调用状态、页码和区域 bbox，不记录密钥；
- 模型不可用时不得使普通 PDF 转换整体失败。

## 4. 阶段一：输入登记

对输入 PDF 执行：

```text
路径标准化
文件存在性检查
PDF 可打开性检查
文件大小和页数读取
SHA-256 计算
document_id 生成
```

`document_id` 应由绝对路径和文件 SHA-256 派生，保证同一文件可幂等处理，文件变化可被检测。

所有页面、文本块、表格、图片和视觉区域 ID 都使用 `document_id` 作为稳定前缀。

## 5. 阶段二：PyMuPDF 页面级预检

预检是轻量、确定性和低成本的，不默认对每一页调用视觉模型。

每页收集：

```text
page_number
width
height
rotation
native_text_chars
native_text_blocks
image_count
image_area_ratio
drawing_count
text_density
encoding_anomaly
layout_complexity
```

预检只负责生成页面特征，不直接替代最终文本解析。

### 5.1 视觉预检的使用原则

建议采用两级预检：

1. 所有页面都执行 PyMuPDF 确定性预检。
2. 只有特征冲突、结构复杂或 OCR 质量异常的页面，才调用 `gpt-5.5` 做视觉分类或区域定位。

视觉预检的目标不是重写页面，而是回答：

```text
页面属于哪一类？
页面中有哪些视觉区域？
哪些区域需要多模态增强？
```

建议视觉预检输出：

```json
{
  "page_type": "complex_layout",
  "regions": [
    {
      "bbox": [80, 120, 520, 460],
      "region_type": "table",
      "need_vision": true
    },
    {
      "bbox": [90, 500, 520, 780],
      "region_type": "figure",
      "need_vision": true
    }
  ],
  "reason": "复杂表格和结构示意图混排"
}
```

模型生成的分类结果必须标记为模型判断，不能伪装成 PDF 原始事实。

## 6. 阶段三：页面分类

页面分类采用页级粒度，同一本 PDF 可以包含多种页面类型。

| 页面类型 | 典型特征 | 主解析路径 | 多模态模型用途 |
|---|---|---|---|
| `native_text` | 原生文字充足、图像少、布局简单 | Docling | 默认不调用 |
| `native_table` | 原生文字存在、矢量线条密集、表格特征明显 | Docling + Camelot/pdfplumber | 表格冲突或合并关系异常时增强 |
| `complex_layout` | 多栏、图文混排、复杂表格或图形密集 | Docling + PyMuPDF + OCR | 对指定复杂区域做视觉理解 |
| `scanned` | 原生文字少，页面主要是扫描图像 | RapidOCR/PaddleOCR | OCR 低置信度或复杂区域增强 |
| `mixed` | 原生文字与图片内容并存 | Docling + OCR | 图片区域和 OCR 难区增强 |
| `figure_page` | 页面主要是图表、流程图或结构示意图 | 图片提取 + OCR | 生成图表语义和关系描述 |
| `blank_or_unreadable` | 没有可用文字或有效图像 | 诊断和有限恢复 | 尝试视觉恢复，失败则保留 warning |

不要把“扫描件”和“复杂图表”当成相同任务：

```text
扫描件的首要目标是准确文字转录
复杂图表的首要目标是理解视觉语义
复杂表格的首要目标是恢复行列和单元格关系
```

## 7. 阶段四：按页面类型路由

### 7.1 普通文字页 `native_text`

```text
PyMuPDF 预检
  ↓
Docling
  ↓
页面级文本块、标题、列表、条款和 bbox
```

Docling 作为主文本来源，PyMuPDF 保留页面事实和兜底能力。没有质量异常时不调用多模态模型。

### 7.2 原生表格页 `native_table`

```text
Docling
  ↓
表格候选检测
  ↓
Camelot / pdfplumber
  ↓
后端结果一致性比较
  ↓
仅对异常表格区域调用 gpt-5.5
```

异常条件可以包括：

- 不同后端行列数明显冲突；
- 存在孤立单元格；
- 复杂表头或合并关系无法恢复；
- 跨页表格表头丢失；
- 表格区域识别成功但内容为空；
- 数字、单位或条款编号出现明显异常。

### 7.3 扫描页 `scanned`

```text
页面渲染
  ↓
RapidOCR/PaddleOCR
  ↓
文字框、顺序和置信度
  ↓
质量评估
  ↓
低质量区域调用 gpt-5.5
```

OCR 仍然是扫描件文字的主解析方式。多模态模型只用于：

- 补充 OCR 失败区域；
- 处理旋转、倾斜和低清晰度文字；
- 识别图片型表格；
- 解释图文混排关系；
- 生成视觉语义描述。

### 7.4 复杂布局页 `complex_layout`

```text
Docling 基础版面解析
  +
PyMuPDF 图片和坐标提取
  +
必要时 OCR
  ↓
按区域调用 gpt-5.5
  ↓
结果融合
```

不建议把整页无条件发送给模型，应尽量先裁剪成：

```text
正文区域
表格区域
图片区域
图表区域
页眉页脚区域
```

### 7.5 图表页 `figure_page`

```text
图片/图形提取
  ↓
区域 OCR
  ↓
gpt-5.5 视觉理解
  ↓
图中文字层 + 视觉语义层
```

视觉模型输出必须区分：

```text
ocr_text：图片中实际可见的文字
visual_summary：图片表达的对象、关系、流程、趋势或分类
```

不能只保留一段不可验证的摘要。

## 8. 表格结果只使用 HTML

### 8.1 持久化规则

表格识别结果最终只持久化 HTML：

```text
TableRecord.html
```

`rows` 可以在解析函数内部作为临时中间结构使用，但不作为最终转换 JSON 的正式表格内容字段；Markdown 也不再作为正式表格字段。

这样可以避免：

```text
rows、Markdown、HTML 三份内容不一致
```

### 8.2 HTML 结构规范

简单表格：

```html
<table>
  <caption>结构构件检测结果</caption>
  <thead>
    <tr>
      <th>构件编号</th>
      <th>裂缝宽度</th>
      <th>损伤等级</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>梁 B1</td>
      <td>0.30 mm</td>
      <td>Ⅱ级</td>
    </tr>
  </tbody>
</table>
```

复杂表头应保留：

```html
rowspan
colspan
```

正式 HTML 只允许结构化表格标签：

```text
table、caption、thead、tbody、tfoot、tr、th、td
```

单元格文本必须进行 HTML 转义。禁止向持久化结果注入 `script`、`style`、`iframe` 等内容。

### 8.3 `TableRecord` 建议字段

```text
table_id
page_number
bbox
html
extraction_method
confidence
needs_review
metadata
```

`metadata` 可以保存结构摘要和诊断信息：

```json
{
  "row_count": 8,
  "column_count": 5,
  "header_rows": 2,
  "has_rowspan": true,
  "has_colspan": true,
  "html_valid": true,
  "quality_flags": []
}
```

这些是来源、质量和结构信息，不是第二份表格正文。

### 8.4 各后端统一输出 HTML

| 后端 | 处理方式 |
|---|---|
| Docling | 从表格网格和单元格关系生成 HTML，尽量保留合并信息 |
| Camelot | 从 DataFrame 生成干净的 `thead/tbody` HTML，不保留 pandas CSS 和索引 |
| pdfplumber | 将二维单元格结果规范化后生成 HTML，处理 `None`、空行和不齐行 |
| gpt-5.5 | 仅在难表格增强时生成候选 HTML，并标记为模型生成候选 |

多模态模型生成的候选 HTML 不应直接覆盖确定性后端结果。应保存为独立候选或替代来源，经过结构和数值校验后才可能升级为主结果。

## 9. 结果融合和去重

### 9.1 文本块融合

推荐基础优先级：

```text
Docling 版面块
  > PyMuPDF 原生块
  > OCR 块
  > 多模态模型转录块
```

但不能简单地按优先级覆盖全部内容：

- 相同区域、相同语义内容只保留质量更高的主结果；
- 不同区域和不同信息全部保留；
- 视觉模型解释不得覆盖原始文字；
- 任何模型生成块都必须标记 `extraction_method=vision:vlm`。

### 9.2 表格融合

同一页的 Docling、Camelot、pdfplumber 和视觉模型结果应按以下步骤处理：

1. 按页码和 bbox 聚类。
2. 规范化 HTML 空白、属性顺序和实体编码。
3. 计算规范化 HTML 内容哈希。
4. 相同内容只保留一个主表格。
5. 其他后端记录到 `alternative_sources`。
6. 内容冲突写入 `quality_flags`。
7. 视觉模型结果标记为 `model_generated_candidate`。

HTML 字符串的缩进和换行差异不应导致重复表格；去重依据应是规范化后的内容。

## 10. 原文、OCR 和视觉结果分层

不得把多模态模型的推断伪装成 PDF 原文。

建议使用以下字段：

```text
text_raw
text_display
text_search
ocr_text
vision_raw
vision_summary
vision_search
```

语义边界：

| 字段 | 含义 |
|---|---|
| `text_raw` | Docling 或 PyMuPDF 提取的原始文字 |
| `text_display` | 面向显示的轻量清理文字 |
| `text_search` | 面向 FTS/BM25/向量检索的归一化文字 |
| `ocr_text` | OCR 实际转写文字 |
| `vision_raw` | 多模态模型原始返回内容，需脱敏和结构校验 |
| `vision_summary` | 对图表或视觉区域的语义描述 |
| `vision_search` | 可进入召回的视觉语义文本 |

对于数值、单位、条款号和规范编号，优先使用原生文本或 OCR 作为事实来源；视觉模型只能作为补充或候选。

## 11. 质量评估

质量报告应覆盖三类指标。

### 11.1 文字质量

```text
原生文字字符数
OCR 字符数
OCR 平均置信度
文字块数量
文本覆盖率
字体编码异常
阅读顺序异常
```

### 11.2 表格质量

```text
HTML 是否有效
行列数量是否稳定
空单元格比例
表头是否存在
rowspan/colspan 是否闭合
不同后端是否冲突
孤立单元格数量
```

### 11.3 视觉质量

```text
图片是否成功提取
图片 OCR 是否成功
视觉模型调用是否成功
视觉摘要是否有 page/bbox 来源
模型输出是否通过 JSON/HTML 校验
```

质量 warning 只写入：

```text
PageRecord.warnings
QualityReport.warnings
StageStatus.warnings
```

不因 warning 阻断自动流水线。

## 12. 推荐回退顺序

### 普通文字页

```text
Docling
  ↓
PyMuPDF
  ↓
RapidOCR
```

### 原生表格页

```text
Docling
  ↓
Camelot / pdfplumber
  ↓
gpt-5.5 难表格增强
  ↓
候选 HTML + warning
```

### 扫描页

```text
RapidOCR/PaddleOCR
  ↓
局部 gpt-5.5
  ↓
整页 gpt-5.5（仅必要时）
  ↓
低质量诊断
```

### 图表页

```text
图片提取
  ↓
图片 OCR
  ↓
gpt-5.5 视觉理解
  ↓
图中文字层 + 视觉语义层
```

原则是低成本、确定性解析优先，高成本、概率性模型后置。

## 13. 建议的输出结构

目标转换契约可以升级为 `knowledge-conversion.v3`，结构如下：

```json
{
  "schema_version": "knowledge-conversion.v3",
  "document_id": "...",
  "source_path": "...",
  "source_sha256": "...",
  "pages": [],
  "blocks": [],
  "tables": [],
  "images": [],
  "visual_regions": [],
  "quality_report": {},
  "status": {},
  "metadata": {}
}
```

### 13.1 文本块

允许的主要抽取方式：

```text
layout:docling
native:pymupdf
ocr:rapidocr
vision:vlm
```

### 13.2 表格记录

表格正文只保存：

```text
html
```

同时保留页码、bbox、来源后端、置信度和质量字段。

### 13.3 图片记录

建议保存：

```text
image_id
page_number
bbox
width
height
image_hash
ocr_text
vision_summary
extraction_method
```

### 13.4 视觉区域记录

复杂图表或局部增强区域可以独立记录：

```json
{
  "region_id": "...",
  "page_number": 20,
  "bbox": [100, 180, 520, 700],
  "region_type": "figure",
  "ocr_text": "...",
  "vision_summary": "...",
  "extraction_method": "vision:vlm",
  "metadata": {
    "model": "gpt-5.5",
    "source": "page:20:region:1"
  }
}
```

## 14. 与切片和索引阶段的接口

PDF 转换阶段只负责产生结构化事实和视觉增强结果。

后续 `chunk.py` 应从 HTML 动态派生两类内容：

```text
完整 HTML
  → context-only 父块

HTML 线性化文本
  → retrieval 子块
```

完整 HTML 用于：

- 生成模型上下文；
- 完整证据保存；
- 表格展示；
- 结构化引用。

线性化文本用于：

- SQLite FTS5；
- BM25；
- embedding；
- 行级表格切片；
- 关键词召回。

不要把原始 HTML 标签直接作为主要 FTS 文本。

视觉摘要可作为独立召回字段，但必须与原文字段区分，避免模型解释被误认为标准原文。

## 15. 推荐实现顺序

1. 增加页级预检结构和页面分类字段。
2. 抽象页面区域裁剪和视觉模型调用接口。
3. 从 GUI 设置读取 Responses URL 和 key，模型固定为 `gpt-5.5`。
4. 统一 Docling、Camelot、pdfplumber 的 HTML 表格生成器。
5. 将 `TableRecord` 的正式表格内容改为 `html`。
6. 增加 HTML 规范化、校验和去重逻辑。
7. 增加扫描页 OCR 质量触发条件。
8. 增加复杂图表的视觉摘要和 `visual_regions` 输出。
9. 修改 `chunk.py`，从 HTML 动态生成检索文本。
10. 修改测试和基准报告，覆盖 native、scanned、complex table、figure 和混合页面。
11. 对大型 PDF 使用单文件独立进程，避免 Docling 原生线程资源清理问题影响批处理。

## 16. 验收标准

### 功能

- 普通原生文字页默认使用 Docling。
- 原生表格页能够输出合法 HTML。
- 扫描页能够使用 OCR，并保留文字框和置信度（若后端提供）。
- 复杂图表能够输出 OCR 文字层和视觉语义层。
- 多模态模型调用使用 GUI 的 URL 和 key，模型为 `gpt-5.5`。
- 多模态不可用时，确定性路径仍可完成转换。

### 数据

- 表格正式内容只有 HTML。
- HTML 能够保留表头、数据体、空单元格以及 rowspan/colspan（识别器提供时）。
- 所有文本、表格、图片和视觉区域都带页码或来源位置。
- 不在任何输出文件中出现 API key 明文。

### 质量

- Docling、PyMuPDF、OCR、表格后端和视觉模型的来源可区分。
- 后端冲突和模型失败会写入 warning。
- warning 不阻断切片、索引和后续自动流程。
- 复杂页面可以按区域调用模型，不要求整本 PDF 全量调用多模态模型。

## 17. 最终架构摘要

```text
PDF
  ↓
PyMuPDF 确定性预检
  ↓
页面分类 / 区域定位
  ↓
┌─────────────────────────────────────────┐
│ native_text      → Docling              │
│ native_table     → Docling + 表格后端    │
│ scanned          → OCR                  │
│ complex_layout   → Docling + OCR        │
│ figure_page      → OCR + gpt-5.5        │
│ 难页/冲突区域     → gpt-5.5 增强          │
└─────────────────────────────────────────┘
  ↓
表格统一为 HTML
  ↓
原文、OCR、视觉语义分层
  ↓
去重、冲突标记、质量评估
  ↓
DocumentConversion
  ↓
chunk → index → retrieve → rerank → generate
```

最终原则：

> 用 Docling、PyMuPDF、OCR 和表格解析器保留可验证事实；用 `gpt-5.5` 处理复杂视觉语义和难页；表格只以 HTML 持久化；所有模型生成内容都必须带来源、方法和质量标记。

