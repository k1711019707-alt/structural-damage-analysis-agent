## Context

当前活动 RAG manifest 和 v2 SQLite 位于 `%LOCALAPPDATA%/YOLO11DamageDesktop/knowledge_base`。项目目录有源码、旧版知识库和运行手册，但没有活动版本的可移植副本；运行时路径解析也默认把相对路径解释为用户数据目录。迁移必须保持现有 SQLite 内容、SHA、schema、索引 parity 和回滚证据不变，并兼容尚未迁移的旧安装。

## Goals / Non-Goals

**Goals:**

- 让项目目录中的版本化 RAG 目录成为迁移后默认活动版本。
- 让活动 manifest 相对路径按 manifest 所在位置解析，而不是硬编码到用户数据目录。
- 保留旧 AppData 活动版本作为只读回滚备份，并让健康检查能明确报告活动来源。
- 用自动化测试覆盖项目 manifest 优先级、相对路径解析和旧安装回退。

**Non-Goals:**

- 不改变 SQLite schema、chunk、检索排序、语义索引或生成协议。
- 不删除 AppData 中的旧数据库、源文件或历史候选目录。
- 不把整个用户知识库管理数据迁移到项目目录。
- 不在本次变更中修复现有 GUI 布局或 RAG 评测失败。

## Decisions

1. **项目 manifest 优先、用户 manifest 兼容回退。** 新增项目目录 `knowledge_base/active_rag.json`。运行时优先读取该文件；不存在时继续读取 `%LOCALAPPDATA%` 下的旧 manifest。这样迁移后的项目可自包含，未迁移安装仍能运行。

2. **相对数据库路径相对于 manifest 所在目录解析。** 活动 manifest 写入 `production-rag-20260919-v11/knowledge_base_v2.sqlite3`，解析基准为项目 `knowledge_base`。绝对路径仍支持，但相对路径禁止逃逸对应知识库根目录。

3. **迁移采用复制而非删除。** 先复制当前活动版本目录和 manifest 到项目目录，再逐文件比较大小与 SHA-256，最后原子写入项目 manifest。原 AppData 版本保留，作为可回滚和审计证据。

4. **激活和回滚沿用当前 manifest API。** `activate_rag()`、`rollback_rag()` 和 `active_rag_status()` 使用同一个解析后的活动 manifest 目标，避免 GUI、脚本和健康检查各自维护路径规则。

5. **项目目录版本使用明确来源字段。** 活动状态增加 `manifest_path`/`storage_scope` 等诊断信息，便于区分 `project` 与 `user_data`，但不改变已有调用方依赖的 `active`、`reason` 和健康字段。

## Risks / Trade-offs

- [项目目录可能不可写] → 一次性迁移只需写入项目；后续激活失败时保留用户目录回退，并返回明确错误，不静默覆盖。
- [项目副本与 AppData 版本漂移] → manifest 记录数据库 SHA，健康检查重新计算 SHA；迁移脚本在切换前后执行一致性验证。
- [旧代码假设相对路径位于用户目录] → 保留通用解析函数的旧行为作为兼容入口，并新增带基准目录的内部解析。
- [项目目录发布包体积增加] → 只迁移当前活动候选及其必要 manifest/证据，不复制全部历史候选和源 PDF。

## Migration Plan

1. 创建项目内 `knowledge_base/production-rag-20260919-v11` 副本。
2. 校验数据库、manifest、build manifest、validation report 和 activation result 的 SHA/大小。
3. 写入项目内 `knowledge_base/active_rag.json`，将数据库路径改为项目内相对路径，并保存旧 manifest 来源信息。
4. 运行 `active_rag_status()`、作用域检索和生产验证只读检查。
5. 失败时删除未激活的项目副本或恢复项目 manifest；AppData 原版本始终不动。

## Open Questions

- 是否在后续发布流程中把项目内 RAG 目录纳入完整便携包，还是仅作为开发/演示资产保留？本次只完成项目本地活动路径，不改变打包策略。
