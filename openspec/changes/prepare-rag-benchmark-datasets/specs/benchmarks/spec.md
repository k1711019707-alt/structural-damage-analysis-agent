## ADDED Requirements

### Requirement: Traceable document benchmark

The project MUST provide a document benchmark whose records identify the source document, production database identity, parsing status, page/chunk counts, warnings, and recommended review pages.

#### Scenario: Regenerate from active production database

- **GIVEN** an active healthy v2 production database
- **WHEN** the benchmark generation script runs
- **THEN** it writes document records with database SHA-256 and real document IDs
- **AND** it does not modify the production database or source files.

### Requirement: Retrieval QA benchmark

The project MUST provide retrieval QA records covering natural Chinese, standard identifiers, clause identifiers, synonyms, table/cross-page context, no-answer, scope isolation, and high-risk review-boundary queries.

#### Scenario: Candidate annotations remain reviewable

- **GIVEN** a query record generated automatically from indexed evidence
- **WHEN** the record is written to the benchmark set
- **THEN** it contains query type, difficulty, relevant document/chunk candidates, and `annotation_status`
- **AND** automatically inferred relevance is marked for human review rather than final gold truth.
