# Design

## Ownership Boundary

本地拥有事实和门禁：`image_name/finding_index`、报告中的损伤类型与等级、报告证据、图片路径、证据一致性、审核状态、溯源以及 `construction_released=false`。本地不得生成或覆盖施工正文。

远端 AI 拥有方案正文：`scope`、`executive_summary`、施工前检查、每个损伤的工法名称、修复工法、理由、适用条件、现场测量、材料、设备、施工步骤、质量控制、验收、安全、假设，以及总体质量、安全、复检、工期假设、排除项和限制。知识库只作为引用和规范参考。

## Input Contract

`DamageRepairPlanner` 改为生成仅含身份和本地事实的 `repair_plan` 输入。保留 `method_id` 等旧字段仅用于读取旧文件兼容，不再将其作为生成内容或 Markdown 的安全控制卡展示。新生成输入不包含本地规则卡的施工文字。

## Remote Contract

远端 schema 必须包含完整 AI 方案字段，并保持单损伤一个请求的有界策略。单项请求的 `work_items` 为一个；总体字段仍由每个原子响应返回，最终按既有去重规则合并。原子响应必须严格校验，缺字段、空字段、身份变化或未知引用均拒绝。

## Assembly

`_assemble_plan` 仅从 `repair_plan` 注入不可变事实和路径，从 `ConstructionPlanDraft` 注入所有方案正文。不得使用 `line[repair_method]`、`method_display_name`、`required_site_verification`、`upgrade_conditions`、`stop_work_conditions` 等本地规则卡正文覆盖 AI 输出。

## Failure Semantics

远端生成失败仍需保留报告、失败类别和审核门禁，但不得把本地规则卡输出标记为完整方案。若生成本地最小安全回退，必须明确 `local_fallback`，正文标注“未生成 AI 施工方案”，不显示 `RC-C02` 等规则卡，也不进入修复渲染。

## GUI

施工方案 Markdown 和审核对话框显示 AI 工法及 AI 方案字段，删除“安全控制卡：RC-C02/RC-U01”等内部规则卡文本。人工审核仍可编辑所有 AI 正文，确认后保持 `construction_released=false`。
