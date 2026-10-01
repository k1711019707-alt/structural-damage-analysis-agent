## ADDED Requirements

### Requirement: Validation SHALL be repeatable and auditable
The system SHALL provide a read-only validation report covering source coverage, database integrity, representative retrieval queries, scope isolation, hierarchy, semantic compatibility, and fallback behavior.

#### Scenario: Full rebuild validation
- **WHEN** an operator runs validation against the managed source directory
- **THEN** the report SHALL include per-document source hash, conversion status, chunk/index counts, warnings, and aggregate coverage without modifying the active database.

#### Scenario: Retrieval quality smoke
- **WHEN** representative natural Chinese, standard-number, clause-number, damage, repair, and safety queries are executed
- **THEN** the report SHALL record query, mode, relevance status, returned markers, anchor/expanded counts, and any missing expected references.

### Requirement: Validation SHALL protect production artifacts
Validation SHALL write to an explicit temporary or versioned output directory and SHALL not overwrite the active database, legacy database, source documents, or user credentials.

#### Scenario: Validation output isolation
- **WHEN** validation completes
- **THEN** active and legacy database hashes SHALL remain unchanged and the report SHALL identify its output directory.
