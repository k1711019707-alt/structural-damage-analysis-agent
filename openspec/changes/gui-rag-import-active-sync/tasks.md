## 1. Production build orchestration

- [x] 1.1 提取可复用的 GUI-safe production RAG build/validate/activate service，接受 legacy catalog 文档清单并返回候选与 active 状态。
- [x] 1.2 在 GUI 导入完成后启动后台同步任务，提供进度和失败诊断。
- [x] 1.3 确保候选使用版本化目录、严格校验和原子激活，失败不替换旧 active。

## 2. Scope consistency

- [x] 2.1 增加从 legacy 文件夹递归解析文档 ID 的稳定方法，并过滤未纳入候选/active v2 的 ID。
- [x] 2.2 文件夹选择保存时覆盖 profile document_ids；空选择清理旧显式 IDs；更新全局 enabled IDs。
- [x] 2.3 激活后刷新 GUI catalog、manifest 摘要和 profile 有效范围，显示 stale/missing IDs。

## 3. Verification

- [x] 3.1 增加导入→构建→验证→激活的服务测试，覆盖严格激活参数和验证失败不伪报成功。
- [x] 3.2 增加 folder selection 覆盖 document_ids、空选择和 active-v2 过滤测试。
- [x] 3.3 运行 focused tests、active status、scope retrieval 和完整测试；完整套件保留两个既有 GUI visual/layout 环境失败，旧 active 未被本次测试修改。
- [x] 3.4 验证导入/生产同步期间保存按钮可用，保存后后台线程继续运行，并覆盖设置对话框关闭后的完成/失败回调。
- [x] 3.5 为 GUI 同步增加目录 SQLite 快照和变更合并队列，覆盖同步中删除后的自动重跑。
- [x] 3.6 为删除最后一个文档增加显式停用 manifest、空范围持久化和 fail-closed 检索测试。
- [x] 3.7 将候选校验失败提示改为简洁用户信息，同时保留诊断报告路径。
