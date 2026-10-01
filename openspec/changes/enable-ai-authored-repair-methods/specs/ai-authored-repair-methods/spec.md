## ADDED Requirements

### Requirement: AI authors one repair method draft for every confirmed finding
The system SHALL require the remote AI construction-plan output to include a non-empty proposed method name, proposed repair method and method rationale for every confirmed finding, in the exact identity order supplied by the application.

#### Scenario: Valid AI method draft is assembled
- **WHEN** the remote model returns one valid method draft for every confirmed finding
- **THEN** the construction plan contains those AI-authored method fields for the corresponding work items

#### Scenario: AI changes finding identities
- **WHEN** the remote model omits, duplicates, adds or reorders a finding identity
- **THEN** the system rejects the remote draft and does not associate its methods with different findings

#### Scenario: AI omits method content
- **WHEN** any proposed method field is missing, empty or invalid
- **THEN** the remote draft fails strict validation and the system follows the conservative fallback path

### Requirement: Application-controlled engineering facts remain immutable
The system SHALL keep finding identity, damage type, damage level, evidence, measurements, provenance, decision status, review status and construction release outside the AI-editable output contract.

#### Scenario: AI attempts to inject control fields
- **WHEN** the model or profile attempts to return a release, decision, evidence, provenance or local method-card field
- **THEN** strict validation rejects the output and no injected value enters the construction plan

#### Scenario: High-risk finding receives a draft
- **WHEN** a confirmed high-risk finding is processed successfully by the remote AI
- **THEN** the plan contains an AI-authored repair method draft while retaining local hold status, site-verification requirements and `construction_released=false`

#### Scenario: Evidence is inconsistent
- **WHEN** the local repair-plan evidence count or identity correspondence is inconsistent
- **THEN** the system keeps the plan blocked and SHALL NOT allow the AI method to enable rendering or construction release

### Requirement: Knowledge material is reference rather than authorship
The system SHALL let AI author the repair method using confirmed evidence, configured profile guidance and any relevant retrieved knowledge while preventing invented source markers, measurements and standards.

#### Scenario: Relevant knowledge is available
- **WHEN** retrieved knowledge contains applicable repair guidance
- **THEN** AI may use it and may cite only source markers supplied by the application

#### Scenario: No knowledge match is available
- **WHEN** no suitable knowledge material is retrieved
- **THEN** AI still drafts a repair method from confirmed evidence and general engineering knowledge while marking field-dependent conditions for site review

### Requirement: AI repair methods require human review
The system SHALL display the AI method name, repair method and rationale in the structured construction-plan review interface and SHALL allow an engineer to edit those fields without changing protected facts.

#### Scenario: Engineer edits and saves a method
- **WHEN** an engineer changes the method fields and saves a draft
- **THEN** the edited fields are persisted with pending confirmation while identities, evidence, decision status, provenance and construction release remain unchanged

#### Scenario: Engineer confirms a method
- **WHEN** an engineer supplies a reviewer and confirms the construction plan
- **THEN** the reviewed method is persisted as confirmed content while `construction_released` remains false

### Requirement: Reviewed method flows to repair rendering
The system SHALL use the engineer-reviewed construction-plan repair method as the method instruction for each subsequent repair rendering item.

#### Scenario: Confirmed plan starts rendering
- **WHEN** a construction plan is confirmed, evidence is consistent and repair rendering is selected
- **THEN** each renderer receives the reviewed repair method associated with the matching image finding

#### Scenario: Plan is not confirmed
- **WHEN** the construction plan remains pending review
- **THEN** the system does not start repair rendering from its AI method draft

### Requirement: Conservative fallback is distinguishable from AI output
The system SHALL retain a deterministic local repair method when remote generation fails and SHALL record that method as local fallback rather than AI-authored content.

#### Scenario: Remote generation fails
- **WHEN** the remote service is unavailable or its structured output fails validation
- **THEN** the system produces the existing conservative local plan, records `local_fallback` as the method source and keeps engineer review required

#### Scenario: Remote generation succeeds
- **WHEN** the remote draft passes all schema, identity and citation checks
- **THEN** the system records `remote_ai` as the method source and preserves the model and generation audit provenance
