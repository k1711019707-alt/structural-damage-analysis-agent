## ADDED Requirements

### Requirement: structured hybrid evidence context
The generation stage SHALL preserve retrieval mode, warnings, anchors, context
groups, channels, evidence types, review flags, and provenance while presenting
bounded evidence groups to the model.

#### Scenario: hybrid anchor has parent context
- **WHEN** a BM25/vector anchor is followed by a hydrated parent
- **THEN** the prompt identifies the anchor as direct evidence, the parent as
  context, retains both source markers, and avoids exact duplicate text.

### Requirement: evidence-type safety
The generation stage SHALL distinguish deterministic text, table rows, OCR,
and model-generated visual descriptions.

#### Scenario: visual summary is model generated
- **WHEN** a chunk has `model_generated=true`
- **THEN** the prompt labels it as non-authoritative visual description and
  requires corroboration before an engineering conclusion.

### Requirement: retrieval fallback audit
The generation stage SHALL retain non-fatal retrieval warnings and SHALL not
misrepresent lexical fallback as successful vector retrieval.

#### Scenario: vector sidecar is unavailable
- **WHEN** retrieval returns BM25 evidence with a semantic warning
- **THEN** generation remains usable, records the warning, and identifies the
  actual retrieval mode.
