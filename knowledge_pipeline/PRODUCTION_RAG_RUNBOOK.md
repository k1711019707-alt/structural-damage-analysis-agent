# Production RAG runbook

生产检索优先使用项目目录 `knowledge_base/active_rag.json` 指向的版本化 v2 数据库；未迁移的旧安装仍回退到用户数据目录下的活动 manifest。旧版 `knowledge_base.sqlite3`、用户数据目录中的旧 `active_rag.json`、源文件和历史候选目录在构建/验证阶段都必须保持不变。

## 0. Project-local active version

当前项目已将活动版本复制到：

```text
YOLO11-seg/knowledge_base/production-rag-20260919-v11/
YOLO11-seg/knowledge_base/active_rag.json
```

项目内 manifest 的 `database_path` 和可选语义索引路径均按 manifest 所在的 `knowledge_base` 目录解析。运行时通过 `active_rag_status()` 报告 `storage_scope=project`；如果项目内 manifest 不存在，则兼容读取 `%LOCALAPPDATA%\\YOLO11DamageDesktop\\knowledge_base\\active_rag.json` 并报告 `storage_scope=user_data`。

迁移采用逐文件 SHA-256 校验的复制方式，用户数据目录的原活动版本保留为回滚备份，不要直接删除或覆盖。项目目录版本与用户目录版本的数据库 SHA 必须一致后，才可把项目 manifest 视为 ready。

## 1. Build a new candidate

每次使用新的版本目录，不要把输出目录指向当前 active 数据库所在目录：

```powershell
$src = "$env:LOCALAPPDATA\YOLO11DamageDesktop\knowledge_base\source_files"
$out = "$env:LOCALAPPDATA\YOLO11DamageDesktop\knowledge_base\production-rag-YYYYMMDD-HHMM"
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\build_production_rag.py $src $out
```

构建器会拒绝以下输出目标：

- 当前 active 数据库或其所在目录；
- legacy `knowledge_base.sqlite3` 或用户知识库根目录；
- 已有 `knowledge_base_v2.sqlite3` 的目录；
- 任何已经存在且非空的目录。

因此不能使用历史候选目录“原地重建”。候选版本目录必须是新建的空目录或尚不存在的路径。

默认构建不会修改 `active_rag.json`。输出包括：

- `converted/*.conversion.json`
- `chunks/*.chunks.json`
- `knowledge_base_v2.sqlite3`
- `build_manifest.json`

构建会先计算每个源文件的 SHA-256。同 SHA 的多个文件只选择一个 canonical 文件转换和索引，其他名称/路径写入该文档的 `aliases`；不得把内容相同的副本作为独立文档进入检索。

`build_manifest.json` 在候选验证前一次性定稿，之后保持不可变。激活成功或失败都不会回写 build manifest；结果单独写入同目录的 `activation_result.json`。

`build_manifest.json` 必须审查：

- `source_file_count`、`canonical_document_count`、`duplicate_alias_count`
- 每个文档的 canonical source 与所有 aliases
- `page_coverage.page_count`、完整 page inventory 覆盖数、文本页数和 `coverage_ratio`
- `failed_pages`、`low_quality_pages`、OCR warnings、conversion warnings
- `quality_score`、chunk/retrieval 数量
- 每个文档及整个候选库的 `required_gate`

默认严格门禁要求：转换状态为 `ready` 或 `success_with_warnings`、索引 ready、完整页面 inventory 覆盖率为 100%、无 failed page、`quality_score >= 0.65`、当前 schema/index stage 完整、source metadata/quality score 覆盖为 100%、retrieval child 与 FTS parity、没有重复 `source_sha256`。`success_with_warnings` 本身不会导致失败；是否失败只取决于 failed pages、页面覆盖、质量分数等明确门禁。

### 远程 API 空白页复核（可选）

对于确定性预检识别为 `blank_or_unreadable` 的候选页，可以显式启用当前 GUI 配置的远程 Responses API：

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\build_production_rag.py `
  "$src" "$out" --remote-blank-review
```

该开关只使用 `gui_settings.json` / `gui_api_config.json` 中已有的 Responses URL、密钥和模型，不创建新的供应商配置，也不使用本地视觉模型。远程 API 必须返回严格 JSON：`blank`、`non_blank` 或 `uncertain`。只有高置信度 `blank` 且没有文字、图形、表格、印章/批注标志的页面才会跳过 OCR 和 chunk；页面仍保留在 `page_inventory`，并记录 `blank_page_reviews` 与 `intentional_blank_pages`。API 超时、密钥缺失、格式错误或 `uncertain` 时，系统继续原有 OCR/failed-page 路径，不自动豁免页面。

## 2. Validate integrity and retrieval relevance

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\validate_production_rag.py `
  "$out\knowledge_base_v2.sqlite3" `
  "$out\validation_report.json" `
  --query-expectations ".\query_expectations.json" `
  --build-manifest "$out\build_manifest.json"
```

验证是只读操作，且必须同时通过：

- 实际数据库 SHA 与 build manifest/显式 `--expected-sha256` 一致
- 当前 required tables/columns 完整，包括 visual/evidence 表和检索指纹字段
- retrieval child 与 FTS 行数一致
- source path/name/SHA 与 quality score 覆盖完整
- 无重复 source SHA
- build required gate 已通过
- 每条代表性查询非零命中，并满足 expected document 或 expected standard

如需验证 GUI 已选择的文件夹或文档在候选 v2/legacy 中是否可用，可增加：

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\validate_production_rag.py `
  "$out\knowledge_base_v2.sqlite3" "$out\validation_report.json" `
  --build-manifest "$out\build_manifest.json" `
  --legacy-db "$env:LOCALAPPDATA\YOLO11DamageDesktop\knowledge_base\knowledge_base.sqlite3" `
  --scope-expectations ".\scope_expectations.json"
```

`scope_expectations.json` 示例：

```json
[
  {"name": "施工方案", "folder_ids": ["verified-folder-id"]},
  {"name": "指定规范", "document_ids": ["verified-document-id"]}
]
```

验证会报告 `resolved_ready_ids`、`v2_available`、`v2_missing`、`legacy_available`、`unavailable` 和 `backend_plan`。空 scope、无效 ID、不可用 scope 都会失败，且不会扩大到未选择文档。若候选必须完全覆盖 GUI scope，增加 `--require-v2-scope`。

查询期望必须使用外部 JSON 文件；不允许在生产激活路径中使用 inline 查询。每条 query 必须至少声明 `expected_document_ids`、`expected_standards` 或 `expected_document_tokens` 中的一种，否则验证配置失败：

```json
[
  {
    "query": "GB 55021-2021",
    "expected_standards": ["GB 55021-2021"],
    "expected_document_ids": ["verified-document-id"]
  }
]
```

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\validate_production_rag.py `
  "$out\knowledge_base_v2.sqlite3" `
  "$out\validation_report.json" `
  --build-manifest "$out\build_manifest.json" `
  --query-expectations ".\query_expectations.json"
```

任意查询全 0、命中错误文档/标准、旧 schema、manifest SHA 不一致或质量门禁失败时，验证状态必须是 `failed`，不能因为数据库中“有一些数据”而标记 ready。

## 3. Activate only after strict validation

推荐将构建、人工审查和验证分开执行。`activate_rag()` 不接受无验证证据的直接激活；最终确认后必须传入 status 为 `ready`、且数据库路径和 SHA 与候选一致的验证报告：

```powershell
@'
from runtime.rag_production import activate_rag, active_rag_status
manifest = activate_rag(
    r"C:\replace\with\candidate\knowledge_base_v2.sqlite3",
    validation_report_path=r"C:\replace\with\candidate\validation_report.json",
    query_expectations_path=r"C:\replace\with\query_expectations.json",
    scope_expectations_path=r"C:\replace\with\scope_expectations.json",
    build_manifest_path=r"C:\replace\with\candidate\build_manifest.json",
    legacy_db_path=r"C:\replace\with\knowledge_base.sqlite3",
    require_v2_scope=False,
)
print(manifest)
print(active_rag_status())
'@ | D:\anaconda\envs\YOLO11-HAI\python.exe -
```

也可在一次构建命令中使用 `--activate`。构建器会先执行完整生产验证并写入 `validation_report.json`，只有所有 required gate 严格通过时才会替换 active manifest；任何失败都会保留原 active manifest：

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\build_production_rag.py $src $out --activate `
  --legacy-db "$env:LOCALAPPDATA\YOLO11DamageDesktop\knowledge_base\knowledge_base.sqlite3" `
  --query-expectations ".\query_expectations.json" `
  --scope-expectations ".\scope_expectations.json"
```

`--activate` 强制要求 `--legacy-db`、外部 `--query-expectations` 和外部 `--scope-expectations`；普通候选构建不要求这些参数。

验证报告使用固定 `production-rag-validation.v3` 契约，并包含 canonical JSON 的 `validation_evidence_hash`。查询期望文件、scope 期望文件和 build manifest 均记录绝对路径、文件 SHA-256、条目数量和解析内容摘要。

激活调用者必须再次显式提供 query expectations、scope expectations、build manifest、legacy DB 路径和 `require_v2_scope`。这些显式参数是权威激活意图，验证报告不能决定或替换它们。激活会要求显式绝对路径及 scope 策略与报告绑定完全一致，再验证文件现场 SHA，并使用显式参数在临时目录重放完整验证。即使有人替换报告中的路径、同步更新 binding SHA 和 evidence hash，也不能把激活重定向到另一组更宽松的验证输入。

候选构建和 active 切换分别使用独占锁。锁文件记录 PID 与 UTC；确认 PID 已失效时可安全恢复 stale lock，PID 仍存活时即使锁很旧也绝不抢占，无法判断 PID 状态时只按保守超时恢复。

激活会记录数据库、query、scope、build manifest、legacy catalog 和 validation report 的初始 SHA。每个快照复制完成后立即计算快照 SHA 并与对应初始 SHA 比较，随后才使用快照执行重放；这可以阻断“同路径替换—复制恶意内容—恢复原文件”的 ABA。重放后、原子写入 active manifest 前还会重新计算全部现场 SHA；任一阶段不一致都失败。

所有健康检查和验证自有 SQLite 查询使用 `mode=ro`、`PRAGMA query_only=ON` 和 busy timeout。损坏的外部数字、`NaN`、`Inf`、非法 JSON 和 SHA 不一致均按 fail-closed 处理。

portable 相对路径只能位于用户知识库根目录之下；包含 `..` 的逃逸路径、drive-relative 路径和 UNC 相对/网络路径不作为 portable manifest 路径接受。

质量告警会合并 `quality_report.warnings` 与 `metadata.conversion_warnings` 并去重。严重告警匹配前会统一大小写、移除空白、常见标点及“第 N 页/Page N”等页码前缀，因此诸如“O C R 失 败”“页面没有可用原生文本 或 OCR 文本”“编码异常”不会被普通复核 warning 遮蔽。

激活 manifest 记录数据库实际 SHA 和验证证据。运行时会重新计算 SHA，并验证当前 schema/index stage、每个文档的 page count 与 `pipeline_pages` 行数、FTS parity、metadata coverage 和可选 semantic sidecar。active manifest 缺少 `database_sha256` 也会被判定为 unhealthy，并保留 legacy fallback。

## 4. Optional semantic sidecar

semantic sidecar 不是必须项。未配置时 v2 词法/结构化检索可正常使用；一旦配置，就必须同时存在 `.npz` 和 `.manifest.json`。健康检查使用 `allow_pickle=False` 实际打开 NPZ，拒绝 object dtype，要求 `vectors` 为二维、`chunk_ids`/`content_hashes` 为安全的一维字符串数组，数组数量、向量维度、schema、chunk 顺序、内容哈希和 corpus fingerprint 全部与 SQLite 一致。相对路径统一相对于用户知识库根目录解析。

## 5. Rollback

激活前保存的 previous manifest 用于回滚：

```powershell
@'
from runtime.rag_production import rollback_rag, active_rag_status
print(rollback_rag())
print(active_rag_status())
'@ | D:\anaconda\envs\YOLO11-HAI\python.exe -
```

回滚不删除候选目录、源文件或 legacy 数据库。回滚后应再次检查 `active_rag_status()`；如果 previous 数据库不满足当前严格 schema，它会被清楚标记为 unhealthy 并使用 legacy fallback，而不会误报 ready。
