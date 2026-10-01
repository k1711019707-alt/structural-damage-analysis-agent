## ADDED Requirements

### Requirement: Runtime retrieval SHALL preserve hierarchical evidence metadata
The runtime retrieval facade SHALL preserve pipeline chunk metadata, anchors, context groups, retrieval mode, scope, and fallback status while remaining compatible with existing chunk consumers.

#### Scenario: Pipeline result propagation
- **WHEN** the v2 pipeline returns anchors and context groups
- **THEN** the runtime result SHALL expose them and each converted chunk SHALL retain semantic, fusion, anchor, expanded, and provenance metadata.

#### Scenario: Legacy result compatibility
- **WHEN** the legacy database adapter is used
- **THEN** the runtime result SHALL retain existing chunks, scope, and fallback behavior with empty hierarchical fields.

### Requirement: Generation context SHALL carry hierarchical diagnostics
The runtime generation context SHALL carry anchors and context groups separately from the text chunks and SHALL preserve review and citation boundaries.

#### Scenario: Damage report context
- **WHEN** a damage report or construction context is built from hierarchical retrieval
- **THEN** its serialized context SHALL include hierarchical diagnostics without removing existing retrieved chunks or source markers.
