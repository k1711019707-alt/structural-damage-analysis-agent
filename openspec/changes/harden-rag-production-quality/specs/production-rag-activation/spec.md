## MODIFIED Requirements

### Requirement: Production health SHALL verify integrity and compatible schema
The system SHALL report an active database healthy only when the database exists, its actual SHA matches the activation manifest, required current tables and columns exist, retrieval-child and FTS counts agree, document source metadata is present, and configured optional sidecars are compatible.

#### Scenario: Database modified after activation
- **WHEN** the actual database SHA differs from `database_sha256`
- **THEN** the active database SHALL be reported unhealthy and legacy fallback SHALL remain available.

#### Scenario: Stale v2 schema
- **WHEN** the database has core v2 tables but lacks required current columns or evidence tables
- **THEN** health SHALL report a schema-compatibility failure instead of `ready`.

#### Scenario: Relative semantic path
- **WHEN** the manifest stores a relative semantic-sidecar path
- **THEN** runtime SHALL resolve it below the user knowledge root and SHALL use it only after compatibility checks pass.

### Requirement: Activation SHALL enforce corpus-quality gates
Activation validation SHALL expose missing pages, OCR failures, duplicate source hashes, missing source metadata, and document-quality thresholds, and SHALL fail configured required gates before manifest replacement.

#### Scenario: Material OCR coverage loss
- **WHEN** a required document exceeds the permitted failed-page threshold
- **THEN** candidate validation SHALL fail and the current active manifest SHALL remain unchanged.
