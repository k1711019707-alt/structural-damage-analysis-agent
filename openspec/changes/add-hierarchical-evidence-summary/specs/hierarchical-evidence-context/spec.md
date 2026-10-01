## ADDED Requirements

### Requirement: Retrieval SHALL assemble hierarchical evidence context
The retrieval stage SHALL distinguish anchor children from expanded context and SHALL group related candidates by document and heading path, with an automatic subsection-first expansion policy.

#### Scenario: Same-subsection expansion
- **WHEN** multiple anchors share the same non-empty `heading_path`
- **THEN** retrieval SHALL create a context group for that heading path, retain all anchors, and include eligible same-subsection parent/child context until the configured budget is reached.

#### Scenario: Chapter fallback
- **WHEN** an anchor has insufficient same-subsection context or no heading path and chapter expansion is requested/selected
- **THEN** retrieval SHALL fall back to the nearest available document-level chapter/page context and SHALL record the expansion reason.

### Requirement: Expanded context SHALL preserve provenance and ordering
Every returned chunk SHALL retain its source marker, document identity, quality metadata, and anchor/expansion status; expanded chunks SHALL be ordered by source position rather than semantic score.

#### Scenario: Anchor diagnostics
- **WHEN** a chunk is directly returned by lexical or semantic candidate ranking
- **THEN** its metadata SHALL include `expanded=false` and an anchor identifier or anchor status.

#### Scenario: Expanded diagnostics
- **WHEN** a chunk is included to complete a subsection or chapter context
- **THEN** its metadata SHALL include `expanded=true`, `expansion_reason`, and the originating anchor ID.

### Requirement: Context limits SHALL be enforced deterministically
The retrieval stage SHALL preserve all selected anchors first and then fill remaining context budget with expanded content in stable source order, without violating document scope.

#### Scenario: Budget overflow
- **WHEN** expanded context exceeds `max_context_chars`
- **THEN** retrieval SHALL retain anchors, truncate or omit lower-priority expanded content deterministically, and preserve source markers for retained chunks.
