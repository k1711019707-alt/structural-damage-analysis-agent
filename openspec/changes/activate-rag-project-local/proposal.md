## Why

当前活动 RAG v2 数据库位于用户 AppData 目录，项目源码目录中的运行、备份和发布资产无法直观看到实际活动版本，导致项目迁移、复现和离线交付时容易出现“源码一份、活动数据另一份”的路径漂移。现在需要把当前已验证的活动版本放到项目目录，并让运行时以项目目录版本为活动来源，同时保留原用户目录版本用于回滚。

## What Changes

- 将当前活动 RAG v2 版本及其构建、转换、分块和验证证据复制到项目目录下的版本化目录。
- 在项目目录建立活动 manifest，使源代码运行时能够解析项目内的活动数据库。
- 更新路径解析和生产运行手册，明确项目内活动版本、用户目录旧版本和回滚关系。
- 迁移前后执行 SHA-256、schema、索引 parity、页覆盖和作用域检索检查。
- 保留 AppData 原活动版本，不在本次变更中删除历史数据。

## Capabilities

### New Capabilities

- `project-local-rag-activation`: 项目目录内的活动 RAG 版本、manifest 解析、健康检查和回滚备份契约。

### Modified Capabilities

- `production-rag-activation`: 活动 manifest 与数据库路径支持项目目录版本，并保持旧用户目录版本可回滚。

## Impact

- 影响 `runtime/app_paths.py`、`runtime/rag_production.py`、RAG 生产运行手册和活动 manifest。
- 影响项目源代码运行、离线演示、便携打包和 RAG 健康检查；不改变 SQLite schema、chunk 内容或检索算法。
- 需要复制约 20 MB 的当前 v2 数据库及其约 10 MB 的构建证据目录，并重新计算迁移后文件哈希。
