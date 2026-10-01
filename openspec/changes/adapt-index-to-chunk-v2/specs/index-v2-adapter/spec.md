## ADDED Requirements

### Requirement: visual evidence persistence
The index SHALL persist every visual region with OCR and layered vision fields,
while only retrieval-role chunks enter FTS5.

#### Scenario: visual OCR chunk is indexed
- **WHEN** a chunk contains a visual-region id and searchable text
- **THEN** the visual region is queryable in the dedicated evidence table and
  the chunk appears once in retrieval FTS.

### Requirement: stable evidence anchors
The index SHALL anchor table and image evidence to a parent chunk when one is
present and SHALL retain links to all related children.

#### Scenario: table has parent and row children
- **WHEN** table parent and multiple row children are indexed
- **THEN** the table registry points to the parent and evidence links include
  every child without replacing the parent anchor.

### Requirement: non-searchable diagnostics
The index SHALL retain table diagnostics and image or visual references that
have no searchable text without placing them in FTS.

#### Scenario: empty visual reference is indexed
- **WHEN** the chunk payload contains a visual reference without OCR text
- **THEN** the evidence registry retains it for review and FTS remains empty
  for that reference.
