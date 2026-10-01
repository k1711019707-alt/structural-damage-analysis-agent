## Context

当前 `RepairRenderWorker` 始终实例化 `FhlRepairRenderer`，并把原图交给 FHL 的 Node 插件。硅基流动并不为该模型提供 OpenAI multipart `/images/edits` 契约；官方文档要求向 `/v1/images/generations` 发送 JSON，其中 `image` 是图片 URL 或 data URL，结果位于 `images[].url` 且 URL 仅短时有效。

`Qwen/Qwen-Image-Edit-2509` 是图片编辑模型，支持 `image`、`image2` 和 `image3`，但明确不支持 `image_size`。本项目每个损伤结果只需一张原始图片，因此只发送 `image`。

## Goals / Non-Goals

**Goals:**

- 允许用户在 GUI 中明确选择 FHL 或硅基流动作为修复渲染供应商。
- 以原始检测图片作为硅基流动图片编辑输入，并将远程结果下载到既有输出目录。
- 保留恢复清单、逐项结果、停止按钮和全成功/部分失败/全失败汇总行为。
- 对 API Key、HTTP 错误、无效响应和下载失败提供有界且脱敏的诊断。
- 兼容既有 `gui_settings.json` 和 FHL 配置。

**Non-Goals:**

- 不嵌入完整的 `fhl-image-studio` 桌面应用或依赖其 Go CLI。
- 不自动把 FHL Key 复制为硅基流动 Key。
- 不实现硅基流动文生图、第二/第三参考图或批量并发。
- 不在没有有效硅基流动 Key 时执行真实收费请求。
- 不关闭硅基流动默认水印；若未来关闭显式水印，应单独处理平台说明中的下游标识义务。

## Decisions

### 使用独立 Python 供应商适配器

新增 `SiliconFlowRepairRenderer`，实现与现有渲染器相同的 `render_one`/`render_many` 结果契约。选择独立适配器而不是修改 FHL Node 插件，是因为两者的端点、请求编码和响应格式不同；供应商分派仅放在 `RepairRenderWorker`。

### 按官方 JSON 图生图契约请求

请求地址由配置的 base URL 规范化后拼接 `/images/generations`。原图读取后编码为带 MIME 类型的 data URL，请求体只包含 `model`、`prompt` 和 `image`。不发送 `image_size`、`guidance_scale` 或 FHL 专有字段。

### 临时 URL 立即原子落盘

成功响应必须包含非空 `images[0].url`。渲染器立即下载 HTTPS 图片到同目录临时文件，校验非空后用 `os.replace` 激活为 `<源文件名>__修復渲染.<格式>`。清单只保存本地路径，不依赖一小时后会失效的远程 URL。

### 配置保持向后兼容

在现有 `ApiSettings` 中增加 `repair_render_provider`、`siliconflow_url`、`siliconflow_key` 和 `siliconflow_model`，默认供应商继续为 `fhl`，因此旧配置加载后的行为不变。设置导出同时清空 FHL 和硅基流动密钥。

### 错误信息有界脱敏

HTTP 状态、平台 `code/message` 和阶段信息进入 `RenderResult.message`，当前硅基流动 Key 在持久化前替换为 `<redacted>`，消息截断。401/403、429、503/504、无图片 URL和下载错误都作为单项失败，由既有 GUI 汇总逻辑报告。

## Risks / Trade-offs

- [硅基流动模型或字段未来调整] -> 模型和 base URL 可配置，并对未知响应显式失败，不静默切换模型。
- [返回 URL 很快过期] -> 在工作线程内立即下载，本地清单不保存远程 URL作为最终产物。
- [base64 请求体增大] -> 当前是一图一请求；限制为源图单张，不引入额外参考图。
- [旧配置没有新字段] -> `from_dict` 以数据类默认值补齐，新字段为纯增量。
- [API Key 泄漏到异常] -> 请求、下载和清单入口统一进行显式替换，并覆盖导出测试。
- [供应商输出改变构件事实] -> 继续使用保守提示词和人工审核边界；渲染图仍标记为沟通预览，不作为施工放行依据。

## Migration Plan

旧用户保持 `fhl` 默认供应商，无需迁移。选择硅基流动时，在设置中填写独立 Key、确认 URL 与模型后保存。回滚时把供应商切回 FHL；已生成图片和清单保留。

## Open Questions

真实账户的模型权限、余额和生成质量只能在用户本机配置有效 Key 后验证；本次实现以官方接口契约和完全模拟的 HTTP 回归测试为准。

## Verification

- 硅基流动渲染器、设置、施工审核衔接、GUI 供应商分派、FHL 回归和便携版打包共 59 项测试通过。
- 相关 Python 模块编译通过，OpenSpec 严格校验通过。
- 未执行真实收费请求；用户提供的 Key 未写入项目、测试或 OpenSpec，须由用户在本机设置页录入后验证账户权限、余额与实际成图质量。
- Docling/PDF 测试不属于本变更的运行依赖，未纳入本次生图接入的验收范围。
