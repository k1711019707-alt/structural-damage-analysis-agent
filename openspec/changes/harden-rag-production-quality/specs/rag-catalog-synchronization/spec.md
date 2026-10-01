## ADDED Requirements

### Requirement: GUI-selected scope SHALL remain available across retrieval backends
The runtime SHALL resolve profile folders/documents from the GUI catalog and query the same document IDs in v2 when available; if the selected scope is absent from v2 but exists in legacy, it SHALL fall back to the same legacy scope without broadening.

#### Scenario: Selected document missing from v2
- **WHEN** a ready GUI-selected legacy document has no corresponding v2 rows
- **THEN** runtime SHALL retrieve from legacy for that selected document and expose a `legacy_scope_fallback` diagnostic.

#### Scenario: Selected document exists in v2
- **WHEN** all selected document IDs are available in the active compatible v2 database
- **THEN** runtime SHALL use v2 and preserve anchors, context groups, source markers, and retrieval warnings.

#### Scenario: Empty selected scope
- **WHEN** neither v2 nor legacy contains usable chunks for the selected scope
- **THEN** runtime SHALL report the scope unavailable and SHALL not query unrelated documents.
