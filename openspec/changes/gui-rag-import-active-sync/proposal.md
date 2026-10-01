## Why

GUI 导入文档后目前只更新用户目录下的 legacy KnowledgeBase，生产生成流程却读取项目目录 active v2 RAG；两者没有自动重建、验证和激活闭环。profile 的文件夹选择也可能被旧的显式 document_ids 静默覆盖，导致界面选择与实际检索范围不一致。

## What Changes

- 将 GUI 导入后的知识库变更接入版本化 production RAG 构建、严格验证和原子激活流程。
- 激活成功后刷新 GUI 的知识库目录、active manifest 和 profile 文档范围，使界面与生产 active RAG 使用同一份有效文档集合。
- 文件夹选择发生变化时，从当前 GUI 管理目录解析文档 ID，替换并清理 profile 的旧显式 document_ids；保存空选择时恢复全局 active 文档范围语义。
- 对 active v2 文档 ID 做一致性校验；失败时保留旧 active 版本、旧设置和可诊断错误，不把未验证候选暴露为生产 RAG。
- 设置窗口在导入或生产 RAG 同步期间仍可点击“保存”；保存只提交当前设置并关闭窗口，导入、构建、验证和激活线程继续由主窗口后台持有并完成。
- 知识库目录在后台同步期间再次发生增删时，按最新目录状态合并排队后续同步；当前候选使用不可变目录快照完成校验，避免“候选与目录来自两个时刻”的误报。
- 删除最后一个知识库文档时原子写入显式停用状态，清空有效文档范围，不允许运行时继续回退使用删除前的 active RAG。
- 保留旧版 legacy 数据库、源文件和此前 active 版本，候选使用新版本目录，不做破坏性删除。

## Capabilities

### New Capabilities

- `gui-rag-import-active-sync`: GUI 导入到生产 active RAG 的自动构建、验证、激活与一致性刷新契约。

### Modified Capabilities

- `production-rag-activation`: 支持 GUI 触发的版本化候选构建和原子激活。
- `generation-profile-scope`: 文件夹选择是 profile 文档范围的来源，保存时清理陈旧显式文档 ID。

## Impact

- 影响 `runtime/damage_workflow_gui.py`、`runtime/knowledge_base.py`、`runtime/rag_production.py`、设置模型与生产构建脚本。
- 影响 GUI 导入等待时间和磁盘占用；重建在后台线程执行，候选失败不会替换当前 active 版本。
- 不改变现有 v2 SQLite schema、检索排序、生成证据协议或远程 API 凭据处理。
