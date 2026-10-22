# 本地 Qdrant

项目只使用 Qdrant 存储向量。SQLite 继续保存文档、正文、BM25 和引用。版本固定为 Qdrant 1.19.2、Python `qdrant-client==1.19.1`。

## Windows 原生服务

在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File deployment/qdrant/start_qdrant.ps1
```

脚本下载官方 Windows 发行包并校验 SHA-256，后台启动服务。支持 `-Executable` 指定已有官方可执行文件。默认 HTTP 地址为 `http://127.0.0.1:6333`，gRPC 为本机 6334。可访问 `/readyz` 检查服务；Windows 原生包不附带 Dashboard 静态文件。

服务文件、日志和 PID 位于 `artifacts/qdrant/`；持久化数据在 `deployment/qdrant/storage/`。关闭终端不会停止服务，重启 Windows 后重新运行启动脚本。停止服务：

```powershell
powershell -File deployment/qdrant/stop_qdrant.ps1
```

若启动时指定了自定义 `-Executable`，停止时传入相同路径；脚本校验 PID 对应的可执行文件路径后才停止进程。

## Docker Compose

```powershell
docker compose -f deployment/qdrant/docker-compose.yml up -d
```

Compose 仅将端口发布到本机，使用独立命名卷持久化数据，关闭遥测。`docker compose ... down` 保留卷；不要使用 `down -v` 删除知识库数据。原生服务与 Docker 选择一种运行，避免端口冲突。切换方式时通过快照恢复，不共享底层存储目录。

应用默认连接本机 HTTP。其他地址和受保护服务可设置：

```powershell
$env:YOLO11_QDRANT_URL = "http://127.0.0.1:6333"
# 仅当服务设置了 API Key 时填写：
$env:YOLO11_QDRANT_API_KEY = "your-service-key"
```

## 索引与旧库迁移

```powershell
python -m knowledge_pipeline.embed work/knowledge.sqlite3 work/semantic.qdrant.json --model BAAI/bge-small-zh-v1.5
python scripts/sync_qdrant.py
```

第二条命令仅用于已有活动 NPZ 的一次性迁移。先验证上传结果、SQLite 身份和摘要，再原子切换活动清单；不重新编码向量。迁移后运行时只读 Qdrant。JSON 描述文件与伴随清单均为元数据，无向量矩阵。迁移完成且验证备份可恢复后，可移除旧 NPZ；迁移工具保留旧格式读取能力，不提供运行时回退。

## 备份与恢复

独立导出原生快照：

```powershell
python scripts/qdrant_snapshot.py export work/semantic.qdrant.json artifacts/qdrant/vectors.snapshot
```

同时保留输出的 `.snapshot.json` 校验信息、SQLite、描述文件、活动清单和编码模型。快照包含知识库数据，应按自己的资料管理。

独立快照恢复时，使用导出校验文件中的 SHA-256：

```powershell
python scripts/qdrant_snapshot.py restore --descriptor work/semantic.qdrant.json --snapshot artifacts/qdrant/vectors.snapshot --sha256 <exported-sha256>
```

便携打包会自动导出快照并记录 SHA-256 到 `knowledge_base/active_rag.json` 的 `qdrant_snapshot`。在目标机启动同版本 Qdrant 后恢复：

```powershell
python scripts/qdrant_snapshot.py restore --active-manifest knowledge_base/active_rag.json
```

恢复拒绝覆盖不同内容的已有 collection；完全匹配时可重复运行。恢复后校验全部向量。桌面 EXE 仍需单独启动 Qdrant，服务本身不嵌入 GUI。

源码 GitHub 同步不包含数据库、原始资料、Qdrant 存储/快照、模型缓存、服务二进制或密钥。
