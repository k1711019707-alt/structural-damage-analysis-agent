# Complete AI Construction Plan

## ADDED Requirements

### Requirement: AI owns the complete construction-plan content
The system SHALL obtain all construction-plan narrative and method fields from the remote AI draft, while local code SHALL preserve only confirmed report facts, identities, paths, provenance, evidence consistency, review state and release gates.

#### Scenario: Remote generation succeeds
- **WHEN** a confirmed report is sent to the construction-plan service
- **THEN** the persisted plan contains AI-authored scope, summary, checks, methods, materials, equipment, procedures, quality, acceptance, safety, inspection and limitations for every finding
- **AND** the plan does not display or copy `RC-C02` or another local rule-card method as its construction content

### Requirement: Human review edits AI content instead of supplying missing content
The system SHALL expose the complete AI-authored plan content to the review UI and SHALL allow the reviewer to edit and confirm it without requiring the reviewer to invent the construction method from an empty placeholder.

#### Scenario: Reviewer confirms a generated plan
- **WHEN** the reviewer edits or accepts the AI draft and confirms it
- **THEN** the edited AI content is persisted with reviewer provenance
- **AND** `construction_released` remains false until the separate release gate is satisfied

### Requirement: Failed generation is not presented as an AI plan
The system SHALL distinguish a failed remote generation from a complete AI plan and SHALL not label deterministic local rule-card text as `remote_ai`.

#### Scenario: Remote generation fails
- **WHEN** any atomic AI draft fails transport or strict validation
- **THEN** the result records the failure and remains blocked from repair rendering
- **AND** no `RC-C02` rule-card text is presented as the generated construction method
