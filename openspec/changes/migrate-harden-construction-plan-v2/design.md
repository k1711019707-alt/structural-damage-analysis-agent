## Context

主线施工方案当前已经只接受经实名确认、证据完整且无待判定项的 `damage-report.v3`，并保留 active-v2 RAG 层级元数据和生成过程的累计文本预览。施工方案本身仍采用简化工法映射、由模型返回完整 `ConstructionPlan`、较短的简体 Markdown；测试允许模型增删证据项或改写基础工法。

外部 `施工方案生成` 目录包含更细的确定性工法卡、证据计数、分项报告依据和繁体施工正文，但其报告 Schema、知识库、GenerationContext 和 GUI 都来自旧快照。该目录只能作为人工迁移来源，不能成为依赖或整目录覆盖源。

## Goals / Non-Goals

**Goals:**

- 在主线 v3、active-v2 RAG 和累计式流式预览之上实现完整施工方案 v2。
- 让证据身份、报告事实、工法、尺度、状态和审计字段完全由本地程序控制。
- 让模型只扩写允许的材料、机具、步骤、质量、安全和验收文字，并严格限制其引用范围。
- 输出可审计 JSON 和面向工程师/施工人员的繁体中文 Markdown。
- 远程失败或输出违规时，使用同一工法卡生成保守本地方案。
- 验证通过后删除被替代的旧施工工法、完整方案模型输出和旧 renderer。

**Non-Goals:**

- 不改变损伤识别、报告 v3 判级、人工确认或证据完整性规则。
- 不恢复像素面积、掩膜比例、自动裂缝尺寸或任何无尺度工程量。
- 不修改生产 RAG 索引、激活清单或语义检索实现。
- 不实现授权工程师的正式施工放行签章流程；生成阶段始终不放行。
- 不引用或修改外部迁移来源目录。

## Decisions

### 1. 保留主线报告和检索边界

`_confirmed_report_payload()` 继续作为施工入口，检查 v3、双重确认、复核人、复核时间、完整性和确定等级。`knowledge_base.py`、`generation_context.py` 和 GUI 流式累计回调保持主线实现，只把新增施工字段接入现有上下文。

选择增量适配而非覆盖外部模块，因为外部快照缺少主线的 active-v2 层级上下文、路由诊断和严格报告确认。

### 2. 本地确定性 RepairPlan 是唯一事实映射

扩展主线 `RepairMethodRule` 和 `RepairPlanLine`，加入 method ID、显示名、decision status、现场核查、升级条件、禁止推断、停工条件、规范提示和报告依据。每项使用 `repair_item_id = image_name#finding_index`。输入只来自已确认报告；检测结果只用于可选图片路径匹配，不参与判级、工法或工程量。

未知类型、高风险/危急等级、结构变形或证据图片无法对应时使用 `RC-U01`/`hold`。其他已知工法仍保持 `site_verification_required`，不产生自动批准状态。

### 3. 模型只返回 ConstructionPlanDraft

新增仅包含允许扩写字段的工作项草稿 Schema。远程请求按每条确定性 repair line 传入事实；模型返回同一身份的一组扩写内容。完整 `ConstructionPlan`、provenance、plan status、release flag、证据计数、工法字段和报告字段均由本地组装。

选择草稿 Schema 而不是接收完整计划后覆盖字段，因为后者仍让供应商返回不应由模型声明的状态，并容易遗漏覆盖字段。

### 4. 全量和分块路径执行同一身份/引用校验

模型返回的 `(image_name, finding_index)` 必须与 repair lines 完全相同、唯一且无新增，并按输入顺序重排。知识库引用必须属于本次 `GenerationContext.retrieved_chunks` 的 source marker 集合。任何失败均抛出受控生成错误，由 GUI 进入确定性本地回退。

分块只拆分工作项草稿，各批次不允许新增项；所有批次合并后再次执行全局身份校验。

### 5. 本地状态机永不自动放行

生成计划初始为 `pending_engineer_review`、`construction_released=false`。证据计数异常设为 `evidence_inconsistent`，任一条目 hold 则设为 `hold`。无论模型输出或 profile 提示词如何，生成阶段都不能出现 `approved_for_construction` 或 `construction_released=true`。

正式放行属于未来独立审批能力，不通过复用生成 Schema 实现。

### 6. 尺度字段保持兼容且为空

保留主线 `component_area_ratio`、`physical_area_mm2` 字段以兼容消费者，但无独立人工量测时必须为 `null`。繁体 Markdown 使用“未提供／待现场复核”或“不适用（无尺度换算）”，不引入外部模块的 `area_ratio: float`。

### 7. 单一繁体 Markdown renderer

用新的施工人员阅读版 renderer 替换旧简体 renderer，正文包含基本资料、证据清单、逐项工法、报告依据、图像占位、质量验收、安全停工、复核和限制。AI 差异、prompt 和完整检索原文只保留在 JSON/manifest。

旧 renderer 在聚焦测试通过后删除，不保留双路由或运行时 feature flag。

### 8. 独立本地规则资源与打包

将施工规则写入主项目 `templates/construction_plan_rules_v2.md` 并加入 PyInstaller spec。该文件是规则说明和知识库候选资料，核心安全约束仍由代码与 Schema 执行。

## Risks / Trade-offs

- [严格身份校验提高远程失败率] → 使用明确 Schema/提示词，并在任何违规时生成完整本地保守方案而非接受部分结果。
- [繁体 Markdown 字段更多、文档更长] → 保持固定章节和逐项结构，机器审计不在正文展开。
- [工法卡涉及工程规范表述] → 只输出“具体条文待工程师核对”，不伪造条款号、参数或正式设计结论。
- [删除旧逻辑影响旧测试或消费者] → 先增加新契约测试并更新消费者，再删除旧函数；保留字段级 JSON 兼容，不保留行为上不安全的旧入口。
- [外部来源后续变化] → 本次迁移完成后以主项目实现和 OpenSpec 为唯一维护基线，不做目录同步。

## Migration Plan

1. 添加新契约的失败测试：身份不可变、模型不能放行、工法不可改、引用受限、无尺度、RC-U01、分块全局校验和繁体正文。
2. 扩展主线 repair plan 与 construction plan Schema，新增本地草稿组装和安全门。
3. 替换远程/本地生成路径与 Markdown renderer，接入 GUI 累计预览和现有 RAG context。
4. 添加规则资源与打包配置，验证无外部路径引用。
5. 聚焦测试通过后删除旧工法映射、完整计划模型输出路径、旧 renderer 和允许 AI 调整证据的测试。
6. 运行编译、聚焦测试、全量维护测试、offscreen GUI smoke、打包资源检查和严格 OpenSpec 校验。

回滚以本次涉及的主项目文件为边界；不改变生产 RAG 数据库或外部迁移来源目录。

## Open Questions

- 真实供应商的完整长方案输出时延和 token 上限需要一次受控在线验证；本地和测试环境先覆盖结构、分块及回退契约。
- DOCX 仍使用现有映射能力；繁体施工 Markdown 的最终人工版式验收与未来完整 DOCX 模板升级分开处理。
