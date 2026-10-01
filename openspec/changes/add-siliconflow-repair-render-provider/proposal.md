## Why

当前修复渲染固定依赖 FHL Node 插件，FHL 上游持续返回 HTTP 502 时没有可选的真实图像编辑服务。用户已选择硅基流动 `https://api.siliconflow.cn/v1` 和图片编辑模型 `Qwen/Qwen-Image-Edit-2509`，需要在保留 FHL 的同时提供可配置、可审计的替代供应商。

## What Changes

- 增加硅基流动修复渲染器，按官方契约调用 `POST /v1/images/generations`，通过 base64 `image` 字段提交原始损伤图片。
- 使用 `Qwen/Qwen-Image-Edit-2509` 作为硅基流动默认模型，不发送该模型不支持的 `image_size` 字段。
- 在收到临时图片 URL 后立即下载到现有 `修復渲染` 目录，并继续写入 `render_manifest.json`。
- 在 GUI 设置中增加修复渲染供应商、硅基流动 URL、API Key 和模型配置，FHL 配置保持兼容。
- API Key 仅进入本机配置和请求头；导出、清单、日志和错误消息必须脱敏。
- 根据供应商配置由后台渲染线程分派到 FHL 或硅基流动实现，继续复用现有停止、结果汇总和人工审核边界。

## Capabilities

### New Capabilities

- `siliconflow-repair-rendering`: 定义硅基流动图片编辑请求、临时结果下载、本地持久化、配置和安全诊断行为。

### Modified Capabilities

无。

## Impact

- 新增硅基流动修复渲染运行模块和单元测试。
- 修改统一设置模型、设置 GUI 和 `RepairRenderWorker` 供应商分派。
- 不增加第三方 Python 依赖，使用标准库 HTTP 客户端。
- 不修改损伤识别、报告生成、施工方案审核或现有 FHL 请求协议。
- 真实生图验证需要用户在本机配置有效的硅基流动 API Key；测试不得持久化或输出凭据。
