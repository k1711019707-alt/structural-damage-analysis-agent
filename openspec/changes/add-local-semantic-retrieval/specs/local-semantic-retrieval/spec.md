## ADDED Requirements

### Requirement: The pipeline SHALL build a reproducible local semantic index
The system SHALL generate a local sidecar embedding index for retrieval-role child chunks, recording model identity, vector dimension, normalization, chunk IDs, content hashes, and source index fingerprint.

#### Scenario: Build semantic index from v2 SQLite
- **WHEN** an operator runs the embedding build script against a valid v2 SQLite database and a locally available model
- **THEN** the script SHALL write a sidecar index containing only retrieval-role children and SHALL report its model and source fingerprints.

#### Scenario: Parent exclusion
- **WHEN** the source database contains context-only parent chunks
- **THEN** those parents SHALL NOT be included in the semantic candidate matrix.

### Requirement: Semantic retrieval SHALL preserve scope and provenance
The semantic retrieval stage SHALL return chunk IDs and cosine similarity scores that can be resolved to the v2 SQLite rows while enforcing requested document scope.

#### Scenario: Scoped semantic query
- **WHEN** a query is executed with `document_ids`
- **THEN** semantic candidates outside the requested documents SHALL be excluded before ranking results.

#### Scenario: Stale vector entry
- **WHEN** a sidecar entry has no corresponding retrieval child or its content hash differs from SQLite
- **THEN** the entry SHALL be ignored and the result SHALL expose an index mismatch diagnostic.

### Requirement: Hybrid retrieval SHALL degrade safely
The retrieval stage SHALL optionally fuse semantic candidates with deterministic lexical and exact metadata channels and SHALL fall back without error when the semantic model or index is unavailable.

#### Scenario: Semantic channel available
- **WHEN** a valid semantic retriever returns candidates
- **THEN** `retrieve.py` SHALL merge candidates by chunk ID, preserve channel diagnostics, and expose semantic scores alongside existing scores.

#### Scenario: Semantic channel unavailable
- **WHEN** the model package, model weights, or sidecar index is unavailable
- **THEN** retrieval SHALL return deterministic lexical results and SHALL expose a non-fatal semantic fallback reason.
