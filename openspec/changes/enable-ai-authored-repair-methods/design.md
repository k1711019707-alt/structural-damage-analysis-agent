## Context

施工方案当前由两层数据组成：`DamageRepairPlanner` 先依据已确认报告生成确定性的规则卡片与安全边界，`ResponsesConstructionPlanService` 再让远端模型扩写材料、步骤、质量和安全等描述字段。远端 schema 没有工法字段，提示词还明确禁止模型生成 repair methods，最终 `ConstructionWorkItem.base_repair_method` 只能复制规则卡片内容。工程师审核界面也把工法整体显示为只读。

本次变更跨越远端结构化输出、方案本地组装、审核持久化和修复渲染衔接。必须同时满足 AI 撰写能力与工程事实保护：AI 可以起草工法，但不能改写检测身份、损伤等级、证据、计量、来源、阻断状态或施工放行。

## Goals / Non-Goals

**Goals:**

- AI 为每个确认损伤生成明确的工法名称、具体修复方法及工法理由。
- 工法草案通过严格 schema、逐项身份、顺序和知识引用校验后进入施工方案。
- 工程师可在结构化审核界面编辑工法，保存草稿或确认审核。
- 修复渲染使用工程师审核后的 AI 工法文本。
- 高风险和证据异常仍保持本地阻断，任何 AI 内容都不能自动放行施工。
- 远端失败时保留可用、可追踪且不冒充 AI 的本地保守回退。

**Non-Goals:**

- 不让 AI 决定损伤等级、证据数量、工程量、造价或施工放行。
- 不取消确定性规则卡片；其继续作为安全边界、停工条件和回退来源。
- 不改变报告人工审核门禁、图像生成供应商协议或 Docling/PDF 流程。
- 不把生成的工法视为正式设计文件或自动施工批准。

## Decisions

### 在远端草稿中增加专用 AI 工法字段

`ConstructionWorkItemDraft` 增加必填的 `proposed_method_name`、`proposed_repair_method` 和 `method_rationale`。字段使用“proposed”命名以明确其为待审核草案，并保持远端 schema 不包含 `method_id`、`decision_status`、`construction_released` 等本地控制字段。相比允许模型返回完整 `ConstructionWorkItem`，该方案显著缩小越权面。

### 最终方案同时保留本地控制卡与 AI 工法

确定性的 `method_id`、`method_name`、`method_display_name` 和 `decision_status` 保持不变，用于安全规则和回退追踪。`base_repair_method` 改为经过校验的 AI `proposed_repair_method`，并新增向后兼容的 `ai_method_name`、`method_rationale`、`repair_method_source` 字段。旧方案缺少新增字段时可按默认值读取；新生成方案明确记录 `remote_ai` 或 `local_fallback` 来源。

### 工法编辑采用明确白名单

审核 GUI 为每个分项提供“AI 工法名称”“修复工法”“工法理由”编辑器。审核合并函数仅允许更新这三个字段和既有描述字段；工法卡 ID、决策状态、损伤事实、证据、来源追踪和施工放行继续从原始方案恢复。这样既满足工程师修订工法，又不扩大对系统控制字段的写权限。

### 高风险项获得工法草案但继续阻断

AI 可以为 `RC-U01` 项起草在现场专项检测、设计复核和批准后才可采用的修复路径。方案状态仍根据本地 `decision_status=hold` 计算为 `hold`，保留停工条件、专项评估和 `construction_released=false`。工程师确认表示完成内容审核，不解除阻断。

### 回退方案使用规则卡片并明确来源

本地回退草稿也填充新增字段，但内容来自确定性规则卡片，`repair_method_source=local_fallback`，审计仍记录 `generation_mode=local_fallback`。远端成功时才标记为 `remote_ai`。不以文案猜测来源，来源由调用路径本地注入。

### 修复渲染从已审核施工方案构造输入

审核确认后，GUI 将 `current_construction_plan.work_items` 转为渲染选择所需的 `image_name/repair_method` 行，优先使用已审核的 `base_repair_method`，并复用现有图像结果匹配与渲染器。若方案未确认或证据不一致，现有门禁继续阻止渲染。

## Risks / Trade-offs

- [AI 工法不适用于现场真实条件] → 强制保留现场复测、适用条件、停工条件、专项评估和工程师审核，不自动放行施工。
- [AI 返回空泛或截断工法] → 新字段为严格必填非空文本，验证失败触发现有本地保守回退。
- [旧方案文件缺少新字段] → 最终 schema 为新增字段提供默认值，加载旧文件时不破坏兼容性。
- [本地规则卡与 AI 工法语义混淆] → GUI 和 Markdown 分开标注“安全控制卡”和“AI 工法草案”，并持久化 `repair_method_source`。
- [审核后渲染仍读取旧 repair_plan] → 渲染分派显式优先构造已审核施工方案行，并用回归测试验证传入渲染器的文本。

## Migration Plan

无需迁移已有检测报告或 `repair_plan.json`。旧 `construction_plan.json` 仍可读取；重新生成施工方案后才会获得 AI 工法字段。回滚时可恢复旧组装逻辑，确定性规则卡、原始 repair plan 和现有审核门禁均保持可用。

## Open Questions

无。正式施工批准继续在系统外完成；本次只改变待审核施工草案的工法作者与审核链路。
