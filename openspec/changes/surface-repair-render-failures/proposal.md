## Why

实际修复渲染批次中，5 个 FHL Images API 请求全部因 `HTTP 502 Bad gateway` 失败，输出目录只有 `render_manifest.json`，但 GUI 仍显示“流程完成”。渲染器还丢弃了子进程 stderr，使清单只记录泛化的“未成功回传”，用户无法区分上游故障、鉴权、参数错误或本地写入问题。

## What Changes

- 在 FHL 子进程返回非零状态时，从 stdout/stderr 提取简短、脱敏、可操作的失败原因并写入渲染清单。
- GUI 对修复渲染结果进行成功、恢复、失败和取消计数。
- 当全部渲染失败时显示“修复渲染失败”而不是“流程完成”，并明确没有生成图片。
- 当部分渲染失败时显示“部分完成”，保留已成功图片并列出失败数。
- 在事件日志中记录逐项失败的文件名和原因，以及实际渲染输出目录。
- 增加返回码诊断、密钥脱敏、全失败和部分失败的回归测试。

## Capabilities

### New Capabilities

- `repair-render-outcome-reporting`: 定义渲染失败诊断、批次结果汇总和真实 GUI 终态。

### Modified Capabilities

无。

## Impact

- 影响 `runtime/fhl_repair_renderer.py` 的子进程失败消息处理。
- 影响 `runtime/damage_workflow_gui.py` 的逐项事件日志和渲染批次终态。
- 不改变 FHL 请求参数、重试策略、图片格式、输出目录或现有 JSON 清单结构。
- 当前上游 `HTTP 502` 仍需服务恢复后才能产生真实渲染图；本变更不伪造成功结果。
