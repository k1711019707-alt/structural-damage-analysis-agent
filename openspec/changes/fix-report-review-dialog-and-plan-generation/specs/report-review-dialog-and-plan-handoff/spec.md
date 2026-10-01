## ADDED Requirements

### Requirement: Report completion opens modal review
After a v3 damage report is generated, the GUI SHALL open a modal review dialog containing the formatted editable report, reviewer input, save-draft action, confirm-and-continue action, and cancel/close action.

#### Scenario: Generated report awaits review
- **WHEN** automatic or manual report generation completes successfully
- **THEN** the review dialog opens and no repair or construction plan starts until confirmation succeeds

#### Scenario: Reviewer cancels or closes the dialog
- **WHEN** the reviewer closes or cancels the dialog without confirmation
- **THEN** the report remains pending, no downstream plan is generated, and the GUI provides an action to reopen the review dialog

### Requirement: Review edits are validated and persisted
The review dialog SHALL allow the structured v3 report to be edited. Saving a draft SHALL validate schema and finding correspondence and persist `edited_pending_confirmation`. Confirmation SHALL additionally require a real reviewer and determinate damage levels.

#### Scenario: Pending edits are saved
- **WHEN** the edited JSON is valid and the reviewer chooses save draft
- **THEN** the updated report is persisted without setting a confirmation timestamp and the dialog remains available

#### Scenario: Confirmation validation fails
- **WHEN** JSON is invalid, the reviewer is missing, a finding is undetermined, or evidence correspondence is invalid
- **THEN** the dialog shows an actionable error, remains open, and does not start plan generation

#### Scenario: Valid report is confirmed
- **WHEN** the edited report passes all confirmation checks and a reviewer confirms it
- **THEN** the report is persisted as `confirmed_by_human`, the dialog closes successfully, and downstream generation is triggered once

### Requirement: Confirmed report handoff is JSON-safe
The GUI SHALL convert the confirmed `DamageReport` into a JSON-compatible mapping before using it as retrieval evidence or generation context. The generation-context boundary MUST serialize supported Pydantic models, dataclasses, paths, and date/time values deterministically and MUST reject unsupported evidence objects clearly.

#### Scenario: Confirmed Pydantic report enters construction planning
- **WHEN** construction planning loads a confirmed `DamageReport` model
- **THEN** profile retrieval and generation-context construction complete without a JSON serialization error and preserve all report fields

#### Scenario: Unsupported evidence object is supplied
- **WHEN** evidence contains an unsupported non-serializable object
- **THEN** context construction fails with a clear evidence-serialization error before a provider request is made

### Requirement: Construction generation status is recoverable
The GUI SHALL finish or fail the active generation preview consistently and SHALL keep the confirmed report available for retry if construction-plan generation fails.

#### Scenario: Construction generation succeeds
- **WHEN** a confirmed report produces a construction plan
- **THEN** plan artifacts are persisted, the preview is marked complete, and plan controls become available

#### Scenario: Construction generation fails
- **WHEN** both remote and local construction-plan generation cannot complete
- **THEN** the preview is marked failed, the error is shown, and the user can retry without regenerating or reconfirming the report

### Requirement: Report-file actions remain contextual
The GUI SHALL keep the report-file row limited to actions required for generated workflow artifacts. The review action SHALL be visible only while review or failed-plan retry requires user action, and the row SHALL NOT provide a generic “读取 Word / PDF” action.

#### Scenario: Confirmed report starts construction generation
- **WHEN** the reviewer confirms the report and construction generation starts
- **THEN** the report-file row does not display “施工方案生成中” or “施工方案已生成” and construction status remains in the existing generation preview

#### Scenario: Report is still pending review
- **WHEN** the reviewer cancels or closes the review dialog
- **THEN** the report-file row displays the reopen-review action

#### Scenario: Report-file row is displayed
- **WHEN** the main workbench is shown
- **THEN** no “读取 Word / PDF” button is present

### Requirement: Workflow progress and action hierarchy are explicit
The GUI SHALL replace the former report-file band with a horizontal workflow progress bar and SHALL place generated-document actions in a row above the primary workflow controls.

#### Scenario: Workflow progress changes
- **WHEN** recognition, report generation, construction generation, or rendering updates overall progress
- **THEN** the horizontal progress bar displays the same current percentage

#### Scenario: Main action area is displayed
- **WHEN** the workbench lays out its right-side action panel
- **THEN** “打开分析报告” and “打开施工方案” appear above “启动” and “生成修复渲染图” without overlap

### Requirement: Project overview prioritizes editable space
The GUI SHALL render the optional project-overview heading more compactly than standard panel titles and SHALL allocate additional stable vertical space to the multiline editor across supported desktop sizes.

#### Scenario: Project overview is displayed
- **WHEN** the main workbench is laid out
- **THEN** the compact title and counter remain readable while the editor is taller than the previous compact field and does not overlap the progress band

### Requirement: Event log aligns with current file
The GUI SHALL align the event-log heading horizontally with the current-file heading and SHALL allow the event-log display to expand vertically within its panel.

#### Scenario: Bottom status row is displayed
- **WHEN** the current-file and event-log panels are visible
- **THEN** their heading top positions match and the event-log display fills the remaining height without overlapping its heading or panel boundary
