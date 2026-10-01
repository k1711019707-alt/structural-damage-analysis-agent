## ADDED Requirements

### Requirement: Frozen retrieval must tolerate stale user overlays

When a frozen application's writable user active-RAG overlay is healthy but does not contain explicitly selected document IDs, and the immutable bundled active-RAG snapshot is healthy and contains all of those IDs, the retrieval operation SHALL use the bundled snapshot for that operation without modifying either manifest.

#### Scenario: GUI profile selects bundled documents after an older user activation

- **GIVEN** the user overlay is active and contains a different document set
- **AND** the frozen bundle contains a healthy active-v2 snapshot with every selected document ID
- **WHEN** scoped report retrieval is requested
- **THEN** retrieval uses the bundled database and semantic index
- **AND** no user or bundled RAG file is written

### Requirement: Incomplete fallback remains fail-closed

If neither the selected user overlay nor the bundled snapshot contains every explicitly selected document ID, the runtime SHALL preserve the explicit unavailable-ID error and SHALL NOT broaden retrieval to unrelated documents.

#### Scenario: Selected ID is absent from both snapshots

- **GIVEN** at least one selected ID is unavailable in both candidate active-v2 databases
- **WHEN** scoped retrieval is requested
- **THEN** retrieval raises an unavailable-ID error listing the missing IDs
