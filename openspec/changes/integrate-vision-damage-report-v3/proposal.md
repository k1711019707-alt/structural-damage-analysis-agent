## Why

当前主线损伤报告仍使用包含像素面积、裂缝几何和规则筛查等级的 `damage-report.v1`，而独立优化模块已经验证了“原图/识别覆盖图 + 最小检测线索 + 规范参考 + 人工确认”的更保守报告流程。该优化必须选择性移植到当前主线，同时修复优化模块中证据项可被 AI 静默增删、审计字段可由 AI 填写、中文等级分块合并不稳定和图片请求无预算等问题，且不得回退已上线的 active-v2 RAG 与流式预览能力。

## What Changes

- 将损伤分析报告契约升级为 `damage-report.v3`，报告输入只保留图片名、损伤类别和识别置信度等定位线索，不再把像素面积、裂缝宽度、面积比例、物理换算或规则筛查等级作为报告判级输入。
- 把原始图片和识别覆盖图作为视觉输入提交给报告模型，并对图片数量、单图字节数、总字节数和分辨率建立确定性预算与可审计降采样。
- 强制报告输出与输入 `(image_name, finding_index)` 一一对应；AI 重排允许，但遗漏、重复或新增证据项会使报告进入不可确认状态并阻断后续施工方案。
- 将生成时间、模型、来源摘要、证据数量、人工复核人、复核时间和复核状态收归本地程序管理，AI 不得声明系统审计或人工复核事实。
- 将损伤等级规范化为固定内部枚举，兼容中英文显示，并在分块生成时按稳定顺序合并、校验全部证据项和重新计算总体最不利等级。
- 在 GUI 中增加报告编辑、复核人填写和显式确认；只有有效、对应关系完整且由非占位复核人确认的报告才能进入修复计划和施工方案。
- 保留当前 `pipeline_search_with_scope`、active-v2 数据库、anchors、context groups、检索告警、来源模式和累计式流式预览，不从独立优化目录覆盖旧版知识库实现。
- 同步调整修复计划、施工方案 Schema、DOCX 映射、Markdown、生成清单和兼容读取，使缺少可靠尺度时相关量统一为不适用或 `null`。
- 增加 v1 旧报告的只读兼容与明确迁移诊断；新生成文件只写 v3，不静默把旧报告当作已确认 v3 报告。
- 主项目不得在源码、配置、启动脚本或运行时路径中引用 `分析报告生成` 目录；优化实现必须复制并适配为主项目自有代码。
- v3 链路通过验证后，删除主项目中已被替代的 v1 几何报告输入、像素/面积规则兜底、旧报告生成分支和绕过人工确认的后续流转逻辑，不保留双写或双路由。
- 更新测试与发布验证，覆盖外部 API 失败、本地保守降级、证据对应阻断、人工确认、中文等级分块、图片预算、v2 RAG 元数据保留和 GUI 工作流。

## Capabilities

### New Capabilities

- `vision-damage-report-v3`: 视觉辅助损伤报告 v3 的输入、生成、证据对应、审计、人工确认、图片预算、下游门控和兼容要求。

### Modified Capabilities

无。当前项目没有已归档的同名主规范，本次以新 capability 固化现有主线与优化模块整合后的完整行为。

## Impact

- 主要影响 `runtime/damage_report_schema.py`、`runtime/responses_damage_report.py`、`runtime/damage_workflow_gui.py`、`runtime/damage_repair_plan.py`、`runtime/concrete_damage_report_mapping.py`、`runtime/construction_plan_schema.py`、`runtime/responses_construction_plan.py` 和生成清单。
- 当前 `runtime/knowledge_base.py`、`runtime/generation_context.py` 与 `knowledge_pipeline/` 的生产 RAG 契约必须保持兼容，只允许增量适配报告上下文。
- 报告 JSON 从 v1 升级为 v3；旧文件允许只读展示，但人工确认和施工方案生成必须经过显式迁移或重新生成。
- `E:\桌面\海之子\分析报告生成` 仅作为迁移来源供人工审查，不是主项目依赖、资源目录或 fallback 路径。
- 不引入新的远程服务；继续使用现有 Responses/Chat 兼容链路和配置，不把 API 密钥写入报告或日志。
