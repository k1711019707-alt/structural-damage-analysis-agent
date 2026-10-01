## Context

当前 GUI 的目录管理使用 `%LOCALAPPDATA%\\YOLO11DamageDesktop\\knowledge_base` 中的 legacy `KnowledgeBase`，而自动报告/方案使用项目目录 `knowledge_base/active_rag.json` 指向的 v2 SQLite。`ActiveRagScopeAdapter` 已禁止 folder-only scope 回退到 legacy SQLite，因此文件夹选择可能无法解析为 active-v2 文档，且旧 profile document_ids 会优先于界面新选择。

## Goals

- 导入成功后由 GUI 自动生成独立、版本化的 v2 候选目录。
- 候选必须通过现有严格数据库完整性、metadata、scope、query 和 provenance 校验后才能激活。
- 激活采用现有原子 manifest 机制，旧 active 版本始终可回滚。
- profile 文件夹选择保存时解析为当前管理目录中的文档 ID，覆盖旧 document_ids，并过滤不在新 active v2 中的 ID。
- GUI 刷新后展示实际 active manifest 和有效文档范围，而不是只展示 legacy 目录状态。

## Non-Goals

- 不删除 legacy SQLite、源 PDF 或历史 active 版本。
- 不在本变更中启用 semantic sidecar 或更改检索算法。
- 不绕过人工工程师复核，不把 needs_review 文档自动提升为 gold evidence。
- 不把用户 API key 写入构建产物、日志或 manifest。

## Decisions

1. **复用生产构建/验证/激活服务。** GUI 只编排已有 `build_production_rag`、`validate_production_rag` 和 `activate_rag` 合约；不在 Qt 层复制索引逻辑。
2. **版本化候选和原子切换。** 每次导入生成 `production-rag-<timestamp>-gui` 候选；候选完整后调用严格验证，再由 `activate_rag()` 更新项目 active manifest。任何异常都保留旧 manifest。
3. **GUI 导入范围作为构建输入。** legacy 目录仍承载原始文件和目录元数据，但构建清单显式列出当前全部有效文档；激活后从 active manifest/source_documents 反向刷新 GUI 文档 ID。
4. **文件夹选择覆盖显式文档范围。** 保存 profile 时，选中的一级文件夹递归解析其文档 ID，写入 `knowledge_base_document_ids`；旧列表被替换，而不是合并。空选择清空 profile 文档 ID，让运行时按全局 active 文档范围处理。
5. **失败可见且不阻断旧版本。** 后台任务报告候选路径、验证失败原因和当前 active 版本；旧 active RAG 继续服务，GUI 不显示未验证候选为已激活。
6. **设置窗口与后台任务解耦。** `KnowledgeBaseImportWorker` 和 `KnowledgeBaseProductionSyncWorker` 由主窗口属性持有，不以设置对话框的生命周期为停止条件。保存按钮不参与 worker gating；对话框关闭后的信号回调先判断控件是否仍存活，只更新设置存储和主窗口状态，避免访问已销毁的 Qt 控件。
7. **候选绑定目录快照。** 启动构建前用 SQLite backup API 固化 legacy catalog，查询/范围 expectations 和严格验证均绑定该快照。活动 worker 期间的新变化设置一个合并 pending 标志，在 worker `finished` 后基于最新目录重建，避免对同一 live SQLite 的跨时刻读取。
8. **空目录使用显式 tombstone manifest。** 零 ready 文档不是候选构建失败，而是用户选择停用知识库。原子写入 `active=false`、`reason=catalog_empty`、空 `source_documents` 的 manifest，并使 `active_rag_status()` 返回 `legacy_fallback=false`；旧 manifest 只作为审计/回滚元数据保留，不参与正常检索。

## Flow

```text
GUI import
  -> legacy catalog/original copy
  -> background build candidate v2
  -> strict validation + scope/query checks
  -> atomic activate project active_rag.json
  -> reload active status/documents
  -> refresh profile document_ids and folder trees
```

## Risks / Mitigations

- 构建耗时较长：后台线程执行并显示阶段进度；候选失败只保留诊断产物，不动 active。
- 项目目录不可写：捕获错误并保持旧 active，提示明确路径和修复动作。
- legacy 文档缺失或转换失败：候选验证失败，不允许激活；保留转换报告。
- folder ID 与 active document ID 漂移：激活后以 active manifest 为准重新映射，清理 stale IDs。
