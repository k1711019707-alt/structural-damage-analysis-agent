## ADDED Requirements

### Requirement: Table deduplication SHALL preserve distinct engineering data
The chunk stage SHALL NOT merge tables solely because they occur on the same page, share a header, share a fixed text prefix, or contain identical text in separate known page regions. Same-backend tables SHALL be merged only when their complete normalized cell matrices are equal and their known table regions strongly overlap.

#### Scenario: Same long header with different indicator rows
- **WHEN** two same-page tables have the same long header but different data or indicator rows
- **THEN** the chunk stage SHALL preserve both table IDs and emit chunks for both tables

#### Scenario: Same backend and different bounding boxes
- **WHEN** one extraction backend emits two tables with different cell content or different table regions
- **THEN** the tables SHALL remain distinct even if their initial normalized text is identical

#### Scenario: Identical content in separate regions
- **WHEN** one extraction backend emits identical normalized table content at two non-overlapping locations on the same page
- **THEN** both table IDs SHALL remain represented because page geometry distinguishes the table instances

### Requirement: Equivalent cross-backend tables SHALL retain provenance
The chunk stage SHALL merge duplicate detections from different extraction backends only when full normalized content is equal or when dimensions, geometry, and full-cell similarity jointly establish a high-confidence equivalence. The canonical table SHALL record each merged detection in `alternative_sources`.

#### Scenario: Exact duplicate from two backends
- **WHEN** Docling and another backend emit the same normalized table on the same page
- **THEN** one canonical table SHALL be emitted and the other table ID, backend, and source content SHALL be retained as an alternative source

#### Scenario: Cross-backend content conflict
- **WHEN** two overlapping cross-backend detections differ materially in a data row
- **THEN** the chunk stage SHALL preserve both tables rather than silently merging the conflict

### Requirement: Real conversion table inventory SHALL survive chunking and indexing
Every non-empty distinct table ID in a conversion payload SHALL remain represented in chunk metadata and in the indexed table inventory unless it is recorded as an equivalent alternative source.

#### Scenario: Current concrete specification conversion
- **WHEN** the 61-table current conversion result is chunked and indexed
- **THEN** the outputs SHALL account for all 61 table IDs without losing the page-34 `fi` table

### Requirement: Standalone semantic retrieval SHALL emit an auditable contract
The standalone semantic retrieval CLI SHALL emit a versioned envelope containing `schema_version`, `query`, `candidates`, `status`, `retrieval_mode`, `relevance_status`, `scope_available`, and `scope_document_ids`. Existing candidate score fields SHALL remain available.

#### Scenario: Semantic hit in requested scope
- **WHEN** a scoped semantic query returns one or more valid candidates
- **THEN** the result SHALL report `retrieval_mode=semantic`, `relevance_status=hit`, the requested scope IDs, and candidates confined to that scope

#### Scenario: Semantic query has no candidate
- **WHEN** semantic retrieval completes successfully but no valid candidate is returned
- **THEN** the result SHALL report a ready stage status and `relevance_status=no_hit` rather than `hit`

#### Scenario: Semantic retrieval is unavailable
- **WHEN** the semantic sidecar, model, or database validation fails
- **THEN** the CLI SHALL write the versioned contract with failed stage status, `relevance_status=unavailable`, a stable warning/error code, and no candidates before returning a non-zero exit code

### Requirement: Downstream stages SHALL preserve retrieval scope and diagnostics
Reranking and generation context SHALL copy `retrieval_mode`, `relevance_status`, `scope_available`, `scope_document_ids`, and upstream retrieval warnings without expanding the requested scope. Generation output SHALL retain `pending_engineer_review`.

#### Scenario: Scoped result passes through rerank
- **WHEN** reranking receives a retrieval payload limited to one document
- **THEN** the rerank result SHALL expose the same scope availability and document ID list

#### Scenario: Scoped result passes through generation
- **WHEN** generation context is built from a scoped retrieval or rerank payload
- **THEN** the generation result SHALL expose the same scope fields, preserve warnings, and keep `review_status=pending_engineer_review`
