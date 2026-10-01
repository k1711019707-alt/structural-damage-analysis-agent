## Why

当前主线已经具备严格的 `damage-report.v3` 人工确认、active-v2 RAG 和流式预览，但施工方案仍是较简化的工法映射与正文输出；独立优化目录补充了确定性工法卡、分项报告依据和繁体施工正文，却基于旧快照，并允许模型改变证据集合、工法和放行状态。需要选择性移植有价值的施工能力，同时把安全约束收归本地代码并删除被替代的旧施工逻辑。

## What Changes

- 将 `RC-R01`、`RC-C02`、`RC-P01`、`RC-S01`、`RC-U01` 确定性工法卡、升级条件、禁止推断、停工条件和稳定 `image_name#finding_index` 追溯移植到主工程自有模块。
- 扩展施工方案 v2 数据契约和本地繁体中文 Markdown，逐项展示原始证据、报告判定依据、工法选择依据、施工前确认、材料机具、步骤、质量验收、安全停工条件、规范提示和图片占位。
- **BREAKING**：AI 仅生成允许扩写的施工内容；不得增删、重复或改写证据身份、报告事实、确定性工法、尺度字段、审核状态或施工放行状态。任何不一致均拒绝远程结果并进入本地保守方案。
- **BREAKING**：`plan_status`、`construction_released`、provenance、证据计数和一致性状态由本地程序构造，不再出现在远程模型可控制的业务草稿中；生成阶段永远不得自动施工放行。
- 保持 `component_area_ratio`、`physical_area_mm2` 为无可靠独立量测时的 `null`，不重新引入检测像素比例作为工程量或工法依据。
- 保留主线 `damage-report.v3` 的实名确认、完整性、确定等级门控，保留 active-v2 RAG 的 anchors/context groups/warnings/route diagnostics/source mode 和累计式流式预览。
- 施工方案生成失败时使用相同确定性工法卡构建本地保守方案，保留 JSON、Markdown 和 manifest 审计并进行凭据脱敏。
- 在新链路测试通过后，删除被替代的旧简化工法映射、模型控制完整方案 Schema、旧 Markdown renderer 和允许模型调整证据集合的测试契约。
- 主项目不得 import、读取、配置或运行时回退到 `E:\桌面\海之子\施工方案生成`；迁移后的代码、规则、测试和打包资源均位于主项目内。

## Capabilities

### New Capabilities

- `hardened-construction-plan-v2`: 确定性工法卡、模型草稿边界、证据一一对应、本地状态机、繁体施工正文、保守回退和旧逻辑退役。

### Modified Capabilities

无。`vision-damage-report-v3` 尚未归档为基础规范；本次在新 capability 中将其既有上游门控作为必须保持的前置条件。

## Impact

- 主要修改 `runtime/damage_repair_plan.py`、`runtime/construction_plan_schema.py`、`runtime/responses_construction_plan.py`、`runtime/damage_workflow_gui.py`、`runtime/settings_models.py`、打包资源和相关测试。
- `runtime/damage_report_schema.py`、`runtime/responses_damage_report.py`、`runtime/knowledge_base.py`、`runtime/generation_context.py` 继续以当前主线为准，只做必要的兼容调用，不从外部目录覆盖。
- `construction-plan.v2` 保持版本名，但远程模型输入输出边界收紧；旧的、由模型控制完整方案对象的输出不再接受。
- 新增主项目自有的施工规则资源，并由 PyInstaller spec 打包；不增加第三方依赖或新的远程服务。
