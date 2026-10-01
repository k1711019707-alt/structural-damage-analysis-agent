## ADDED Requirements

### Requirement: Default review is human-readable
The report-review dialog SHALL open on a Chinese structured form rather than editable serialized JSON and SHALL organize editable content into overall information, damage findings, and review/limitations views.

#### Scenario: Reviewer opens a generated report
- **WHEN** the damage-report review dialog opens
- **THEN** the overall information view is selected and normal review fields are shown with human-readable Chinese labels
- **AND** raw JSON is not the primary editing surface

#### Scenario: Optional data is absent
- **WHEN** an optional report field contains `null`
- **THEN** its form control is blank or displays a human-readable empty state and does not display the literal text `null`

#### Scenario: Long report content is displayed
- **WHEN** a summary, reason, evidence, recommendation, note, or limitation exceeds the visible line width
- **THEN** the text wraps within its editor and remains reachable without a page-level horizontal scrolling workflow

### Requirement: Damage values use localized controls
The dialog SHALL display damage levels with Chinese labels and SHALL preserve the canonical `undetermined`, `low`, `medium`, `high`, and `critical` schema values.

#### Scenario: Existing level is loaded
- **WHEN** a report contains a canonical damage level
- **THEN** the corresponding Chinese option is selected in the overall or finding level dropdown

#### Scenario: Reviewer changes a level
- **WHEN** the reviewer selects a Chinese damage-level option and saves
- **THEN** the reconstructed report contains the corresponding canonical schema value

### Requirement: Finding review preserves evidence identity
The dialog SHALL provide editable controls for finding business content while keeping each finding's image name, finding index, structured standards list, and correspondence relationship protected from accidental editing.

#### Scenario: Reviewer edits finding content
- **WHEN** the reviewer changes damage type, level, reasoning, visual basis, uncertainty, observed evidence, risk interpretation, recommendation, or confidence note
- **THEN** those changes are validated and persisted while image name and finding index remain unchanged

#### Scenario: Standards basis is displayed
- **WHEN** a finding has structured standards references
- **THEN** the default view displays readable source id, name, and role text without requiring the reviewer to interpret raw JSON
- **AND** unresolved source or page details are not fabricated

### Requirement: System-owned report data is read-only
The dialog MUST preserve the report schema version, provenance, integrity, finding count, finding order, and finding identities from the last valid current report during every draft or confirmation reconstruction.

#### Scenario: Form report is reconstructed
- **WHEN** the reviewer saves a draft or confirms the report
- **THEN** only allowlisted business and human-review fields are copied from controls
- **AND** system-owned values are restored from the current report before persistence validation

### Requirement: Advanced data is inspectable but not editable
The dialog SHALL provide the complete report serialization in an explicitly secondary advanced-data view, and that view SHALL be read-only and synchronized from the structured form.

#### Scenario: Reviewer selects advanced data
- **WHEN** the reviewer opens the `原始数据` view
- **THEN** the current form state is rendered as formatted JSON in a read-only control

#### Scenario: Reviewer returns to normal review
- **WHEN** the reviewer leaves the advanced-data view
- **THEN** no direct JSON editing is required to continue reviewing or saving the report

### Requirement: Existing draft and confirmation gates remain enforced
Structured form saves SHALL reuse the existing report persistence service and confirmation validation, including evidence correspondence, reviewer identity, determinate finding levels, status transitions, and downstream construction-plan gating.

#### Scenario: Reviewer saves a valid draft
- **WHEN** the structured form is schema-valid and the reviewer saves without confirming
- **THEN** the report is persisted as `edited_pending_confirmation` and the dialog remains open

#### Scenario: Reviewer confirms valid content
- **WHEN** the reviewer is identified, every finding level is determinate, and the structured report passes correspondence validation
- **THEN** the report is persisted as `confirmed_by_human` and the existing construction-plan workflow may continue

#### Scenario: Confirmation is invalid
- **WHEN** a required value, reviewer, determinate level, or correspondence validation fails
- **THEN** the dialog remains open, displays an actionable error, and does not trigger construction-plan generation
