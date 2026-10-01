## Why

检测结果表目前只显示编号、损伤类型、置信度和状态，虽然检测结果已经包含可追溯的 `screening_severity.level`，用户仍无法在主工作台直接查看损伤初筛等级。需要把这个已有检测字段以明确的“初筛等级”语义显示出来，同时避免与报告阶段经视觉模型和人工复核形成的辅助损伤等级混淆。

## What Changes

- 在主工作台“检测结果”表中新增“损伤等级”列，显示检测阶段的影像面积比例初筛等级。
- 将 `undetermined`、`low`、`medium`、`high` 映射为用户可读的“待判定”“轻微”“中等”“严重”，未知或缺失值安全降级为“待判定”。
- 无检出或处理失败行在等级列显示“—”，不伪造损伤等级。
- 同步检测结果 CSV 导出列，并保持损伤类型筛选、表格布局和现有详情交互兼容。
- 增加 GUI 合同测试，覆盖表头、等级映射、缺失值、空结果和导出行为。
- 修复报告人工确认到施工方案生成之间的主按钮状态：下一阶段仍在执行时保持“停止”外观，而不是提前恢复为“启动”。
- 修复检测结果“编号”重复显示模型内部零基索引的问题，改为结果表范围内从 1 开始的连续展示序号，同时保留内部证据索引不变。
- 修复已勾选修复渲染但确认后的 `hold` 施工方案仍跳过渲染的问题：允许对已由工程师确认的 `hold` 方案生成非施工放行的预览图，证据不一致方案继续阻断。

## Capabilities

### New Capabilities

- `detection-screening-severity-display`: 规定检测结果表如何显示、降级和导出检测阶段的损伤初筛等级。
- `post-report-review-workflow-control`: 规定报告确认后进入施工方案生成阶段时主按钮的状态语义。
- `detection-result-display-numbering`: 规定检测结果表使用面向用户的连续编号，并与内部证据索引解耦。
- `reviewed-plan-render-continuation`: 规定已审核施工方案在勾选渲染时的继续条件，以及证据不一致时的阻断边界。

### Modified Capabilities


## Impact

- GUI：`runtime/damage_workflow_gui.py` 的检测结果表构建、结果行填充、CSV 导出及报告确认后的工作流按钮状态。
- 测试：`tests/test_damage_workflow_gui_contract.py` 与 `tests/test_report_review_dialog_handoff.py` 中的表格合同、交互和报告到施工方案衔接测试。
- 数据合同保持不变，继续读取已有 `damage_findings[].screening_severity.level`；不修改 YOLO 推理、面积比例阈值、报告分级或施工方案逻辑。
