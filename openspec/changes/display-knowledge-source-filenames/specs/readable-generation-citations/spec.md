## ADDED Requirements

### Requirement: Generation citations display authoritative source filenames
The system SHALL render knowledge-base citations in damage-report and construction-plan Markdown with the authoritative source basename, normalized page/location, and original internal KB marker.

#### Scenario: Report citation resolves to an indexed PDF
- **WHEN** report content contains a marker whose retrieved metadata identifies `GB 50010-2010 混凝土结构设计规范-上.pdf` at page 42
- **THEN** report Markdown displays that filename, page 42, and the original KB marker

#### Scenario: Construction citation resolves to an indexed PDF
- **WHEN** construction-plan content contains a marker whose retrieved metadata identifies a source at page 31
- **THEN** construction-plan Markdown displays the source basename, page 31, and the original KB marker

### Requirement: Citation metadata is deterministic and private-path safe
The system MUST derive source filenames from retrieved or active-v2 index metadata, MUST NOT trust model-authored filenames as provenance, and MUST NOT expose source directory paths in user-facing Markdown.

#### Scenario: Indexed source uses a Windows path
- **WHEN** authoritative metadata contains a Windows source path
- **THEN** the citation displays only the final filename and omits all parent directories

#### Scenario: Duplicate retrieved markers are cataloged
- **WHEN** multiple retrieved chunks produce the same source marker and source metadata
- **THEN** the persisted citation catalog contains one deterministic entry for that marker

### Requirement: Citation display survives human review persistence
The system SHALL persist the citation catalog in each generated artifact envelope and SHALL reuse that catalog when human-reviewed report or construction-plan Markdown is saved.

#### Scenario: Reviewed report is saved
- **WHEN** an engineer confirms or edits a report containing a resolved KB marker
- **THEN** the regenerated report Markdown retains the same readable filename, location, and internal marker

#### Scenario: Reviewed construction plan is saved
- **WHEN** an engineer confirms or edits a construction plan containing a resolved KB marker
- **THEN** the regenerated construction Markdown retains the same readable filename, location, and internal marker

### Requirement: Unresolved citations remain auditable
The system SHALL preserve an unresolved internal marker and label its source as unresolved instead of inventing a filename or silently removing the citation.

#### Scenario: Marker is absent from the persisted catalog
- **WHEN** generated content contains a KB marker with no unambiguous authoritative catalog entry
- **THEN** Markdown retains the marker and identifies the source as unresolved
