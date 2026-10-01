## ADDED Requirements

### Requirement: Minimal non-geometric report evidence
The system SHALL build report prompts from image identity, detection class, detection confidence, project overview, visual attachments, and selected reference evidence only. It MUST NOT include pixel area, pixel length, crack width, physical dimensions, area ratio, calibration conversions, or rule-derived screening severity as report grading evidence.

#### Scenario: Legacy geometric fields are present in detection output
- **WHEN** a detection summary contains boxes, mask measurements, crack geometry, calibration, or screening severity
- **THEN** the report request excludes those fields and retains only the minimal locating hints

### Requirement: Bounded visual evidence attachments
The system SHALL attach original images and recognition overlays only for images referenced by report findings. It SHALL remove image metadata, normalize orientation, constrain resolution, and enforce configured per-image, count, and total-byte budgets before any network call.

#### Scenario: Images fit after deterministic normalization
- **WHEN** referenced images can be normalized within all visual budgets
- **THEN** the request includes the normalized attachments and the audit records source role, dimensions, bytes, sent dimensions, sent bytes, and resize status without recording image bytes

#### Scenario: Visual budget cannot be satisfied
- **WHEN** referenced visual evidence still exceeds a hard budget after allowed normalization
- **THEN** the remote request is not sent and the system produces a clearly marked conservative fallback with the budget failure reason

### Requirement: AI draft excludes system and review facts
The remote model SHALL return only report business content. Report version, generation timestamp, model identity, source summary path, evidence count, review status, reviewer, review time, and integrity status MUST be created or overwritten by local code.

#### Scenario: Model attempts to return audit or review fields
- **WHEN** a provider emits fields outside the strict draft schema
- **THEN** validation rejects the response and no model-supplied audit or human-review fact is persisted

### Requirement: Fixed damage-level vocabulary
The system SHALL store finding and overall levels using `undetermined`, `low`, `medium`, `high`, or `critical`. It SHALL normalize supported Chinese display aliases when reading editable or compatibility data and SHALL reject unknown values.

#### Scenario: Chunked findings use normalized levels
- **WHEN** more than eight findings are generated in multiple batches
- **THEN** the overall level is recomputed from normalized levels using the defined severity order

### Requirement: Exact evidence correspondence
Every generated report SHALL contain each input `(image_name, finding_index)` exactly once and SHALL contain no additional identity. Reordering is allowed, but the persisted order SHALL be normalized to input order.

#### Scenario: Provider omits, duplicates, or adds a finding
- **WHEN** the returned identity multiset differs from the input identity list
- **THEN** remote generation is rejected, the mismatch is recorded, and the report cannot be confirmed or used downstream

#### Scenario: Provider returns all findings in another order
- **WHEN** all identities are present exactly once but returned in a different order
- **THEN** the system accepts the content and persists findings in input order

### Requirement: Conservative local fallback
If remote visual generation fails, the system SHALL persist all input identities with `undetermined` levels, SHALL NOT infer grades from confidence or geometry, and SHALL preserve the failure reason after credential redaction.

#### Scenario: Remote provider is unavailable
- **WHEN** retries are exhausted or visual preparation fails
- **THEN** JSON and Markdown fallback artifacts are written atomically and clearly state that no visual grade was produced

### Requirement: Human review and confirmation
The GUI SHALL allow editing a valid v3 report and SHALL require a non-placeholder reviewer before confirmation. Confirmation SHALL set the review status and timestamp locally only after schema, identity, level, and integrity checks pass.

#### Scenario: Reviewer or determinate level is missing
- **WHEN** the user requests confirmation with an empty/placeholder reviewer or any `undetermined` finding
- **THEN** confirmation is rejected with an actionable message and the report remains unconfirmed

#### Scenario: Valid report is confirmed
- **WHEN** a reviewer supplies their identity and the edited report passes all confirmation checks
- **THEN** the persisted report becomes `confirmed_by_human` with a local UTC review time

### Requirement: Downstream confirmation gate
Repair-plan and construction-plan generation SHALL require a v3 report with `confirmed_by_human` status and passing integrity checks. A legacy, pending, fallback-undetermined, or correspondence-invalid report MUST NOT enter the downstream workflow.

#### Scenario: User tries to generate a plan from an invalid report
- **WHEN** the persisted report does not satisfy every downstream gate
- **THEN** no repair or construction plan is generated and the GUI explains the unmet requirement

### Requirement: Production RAG metadata preservation
Report generation SHALL continue to use the active-v2 scoped retrieval facade and SHALL preserve retrieval mode, anchors, context groups, warnings, route diagnostics, source mode, and evidence hash in the generation context and manifest.

#### Scenario: Hierarchical active-v2 retrieval supplies context
- **WHEN** the selected report profile retrieves active-v2 anchors and expanded context
- **THEN** the report prompt and manifest retain the current hierarchy and provenance without invoking legacy retrieval

### Requirement: Stable streaming preview
The report service SHALL preserve the mainline cumulative-text callback contract for both Responses and compatible Chat streaming. The GUI preview SHALL show cumulative in-progress output and replace it with the final validated report after completion.

#### Scenario: Provider streams structured output
- **WHEN** text deltas arrive from the provider
- **THEN** callbacks receive cumulative text in arrival order and final persistence occurs only after complete schema validation

### Requirement: Scale-free document and plan outputs
Markdown, DOCX, repair-plan, and construction-plan outputs SHALL represent unavailable geometric quantities as `null` or “不适用（无尺度换算）” and SHALL base methods and quantities on human-reviewed findings and site verification requirements.

#### Scenario: No reliable physical scale is available
- **WHEN** a confirmed report is rendered or passed to planning
- **THEN** no pixel-derived quantity, area ratio, crack width, or physical repair quantity is presented as an engineering fact

### Requirement: Legacy report compatibility
The system SHALL identify legacy report versions and allow read-only display, but SHALL require v3 regeneration or an explicit validated migration before confirmation or downstream use.

#### Scenario: Existing v1 report is opened
- **WHEN** a user loads a `damage-report.v1` artifact
- **THEN** the GUI identifies it as legacy, leaves the source unchanged, and blocks confirmation and plan generation until a v3 report exists

### Requirement: Self-contained migration and legacy retirement
The main project MUST own every migrated implementation and MUST NOT import, read, or fall back to code or resources under the external `分析报告生成` directory. After the v3 path passes focused verification, the project SHALL remove the replaced v1 geometric report-generation models, prompts, local grading fallback, and unreviewed downstream path while retaining only explicit legacy-version detection.

#### Scenario: Main project runs after migration
- **WHEN** the external optimization directory is unavailable or moved
- **THEN** report generation, review, persistence, and downstream gating continue to run exclusively from files inside the main project

#### Scenario: Repository is scanned for retired behavior
- **WHEN** implementation and focused verification are complete
- **THEN** no active report-generation path emits v1, grades from pixel-derived geometry, bypasses human confirmation, or references the external optimization directory
