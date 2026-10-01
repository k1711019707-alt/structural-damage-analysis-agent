## ADDED Requirements

### Requirement: Construction plans have a structured human-review interface
The application SHALL open a Chinese structured construction-plan review dialog after generation and SHALL organize content into overall information, work items, general requirements, review, and read-only advanced-data views.

#### Scenario: Generated plan opens for review
- **WHEN** a construction plan is generated and persisted
- **THEN** the application schedules the construction-plan review dialog and displays a clear warning that review does not release construction

#### Scenario: Reviewer closes the dialog
- **WHEN** the reviewer closes or rejects the dialog without confirmation
- **THEN** the plan remains pending review and a visible `审核施工方案` action remains available

### Requirement: Review edits only descriptive construction content
The review dialog SHALL allow edits only to descriptive plan fields and MUST preserve work-item identity, report facts, deterministic method selection, decision status, evidence counts, scale fields, figures, stop-work conditions, excluded conclusions, knowledge references, schema version, and provenance.

#### Scenario: Reviewer saves descriptive edits
- **WHEN** the reviewer edits scope, summary, checks, materials, equipment, procedure, quality, acceptance, safety controls, assumptions, or general requirements and saves a draft
- **THEN** those fields are persisted and the review remains pending

#### Scenario: Protected state is reconstructed
- **WHEN** the dialog reconstructs or persists a plan
- **THEN** all protected fields are restored from the last validated persisted plan even if widget state or caller input attempts to replace them

### Requirement: Advanced plan data is inspectable but not editable
The dialog SHALL expose the complete current construction plan in a secondary read-only advanced-data view synchronized from the structured form.

#### Scenario: Reviewer opens advanced data
- **WHEN** the reviewer selects the advanced-data tab
- **THEN** the current form state is rendered as formatted JSON in a read-only control

### Requirement: Construction review is persisted without granting release
The application SHALL persist reviewer identity, review time, notes, and review status atomically inside the existing construction-plan envelope, and MUST keep `construction_released=false`.

#### Scenario: Reviewer saves a draft
- **WHEN** a schema-valid plan is saved without confirmation
- **THEN** its review status becomes `edited_pending_confirmation`, its envelope metadata is preserved, and the dialog remains open

#### Scenario: Reviewer confirms without an identity
- **WHEN** confirmation is requested with a blank reviewer
- **THEN** validation fails, the dialog remains open, and no confirmed review is persisted

#### Scenario: Engineer confirms a valid plan
- **WHEN** an identified engineer confirms a schema-valid construction plan
- **THEN** the review status becomes `confirmed_by_engineer`, reviewer, UTC review time, and notes are persisted, and `construction_released` remains false

### Requirement: Review cannot erase blocking engineering dispositions
Human review MUST NOT change `plan_status`; plans with `hold` or `evidence_inconsistent` SHALL remain blocked after confirmation.

#### Scenario: Hold plan is confirmed
- **WHEN** an engineer confirms a plan whose status is `hold`
- **THEN** the persisted plan remains `hold` and repair rendering remains unavailable

#### Scenario: Evidence-inconsistent plan is confirmed
- **WHEN** an engineer confirms a plan whose status is `evidence_inconsistent`
- **THEN** the persisted plan remains `evidence_inconsistent` and repair rendering remains unavailable

### Requirement: Repair rendering is gated by construction review
The application SHALL NOT start or enable repair rendering until the construction review is confirmed and the engineering disposition is `pending_engineer_review`.

#### Scenario: Generation completes before review
- **WHEN** a construction plan is persisted but has not been confirmed by an engineer
- **THEN** checked automatic-render preferences do not start rendering and rendering controls remain blocked

#### Scenario: Review is confirmed for a non-blocked plan
- **WHEN** the plan review becomes `confirmed_by_engineer` and `plan_status` is `pending_engineer_review`
- **THEN** repair rendering becomes available and a previously selected automatic-render preference may start it

### Requirement: Construction event logs contain user-level milestones
The permanent construction event log SHALL contain stage start/completion, review waiting/confirmation or pending-close, concise fallback, and terminal failure events, and SHALL NOT record provider attempts, compatible route names, local assembly details, or draft-persistence internals.

#### Scenario: Detailed worker progress is emitted
- **WHEN** the worker reports request attempts, compatible transport route, validation/assembly, or persistence progress
- **THEN** the status and current-file surfaces continue to receive the detail and no corresponding event-log row is appended

#### Scenario: Construction stage completes
- **WHEN** a plan is successfully persisted
- **THEN** the event log records construction-stage completion followed by waiting for human review
