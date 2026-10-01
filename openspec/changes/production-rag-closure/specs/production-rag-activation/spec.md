## ADDED Requirements

### Requirement: Production activation SHALL be versioned and atomic
The system SHALL activate a verified v2 database through a manifest that records schema, source fingerprints, counts, build time, semantic sidecar compatibility, and a rollback target; the legacy database SHALL remain unchanged.

#### Scenario: Activate verified build
- **WHEN** a v2 build passes structural and source coverage checks
- **THEN** activation SHALL write a versioned manifest atomically and identify the v2 database as active.

#### Scenario: Failed build
- **WHEN** conversion, indexing, or validation fails
- **THEN** the active manifest and legacy database SHALL remain unchanged and the failure SHALL be reported without partial activation.

### Requirement: Runtime SHALL provide a recoverable fallback
The runtime SHALL prefer a healthy active v2 database and SHALL fall back to the legacy database when the manifest, v2 schema, or semantic sidecar is unavailable or incompatible.

#### Scenario: Missing active manifest
- **WHEN** no active v2 manifest exists
- **THEN** legacy retrieval SHALL remain available and the result SHALL expose the fallback reason.

#### Scenario: Rollback
- **WHEN** an operator requests rollback
- **THEN** the previous manifest or legacy database SHALL be restorable without deleting source documents or generated outputs.
