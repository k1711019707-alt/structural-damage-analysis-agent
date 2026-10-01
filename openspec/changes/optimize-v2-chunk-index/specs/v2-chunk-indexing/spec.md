## ADDED Requirements

### Requirement: Preserve v2 chunk evidence
The indexer SHALL persist every valid `knowledge-chunks.v2` chunk with its outer fields, raw/contextual/search text, parent relationship, page numbers, heading path, clause number, extraction methods, quality flags, and content hash.

#### Scenario: Metadata survives indexing
- **WHEN** a v2 payload contains a table-row child with `needs_review` and page metadata
- **THEN** the SQLite chunk row and returned manifest retain those values without dropping the row

### Requirement: Role-aware weighted FTS
The indexer SHALL put only chunks whose metadata `retrieval_role` is `retrieval` into the primary FTS table and SHALL index body search text plus heading, clause, standard, table, and image fields as separate weighted columns.

#### Scenario: Parent does not pollute primary search
- **WHEN** a payload contains a context-only parent and retrieval children
- **THEN** the parent is queryable by relation but is absent from the primary retrieval FTS rows

### Requirement: Incremental and transactional indexing
The indexer SHALL use source/schema fingerprints to skip unchanged documents, replace one changed document atomically, and produce counts for documents, pages, parents, children, tables, images, duplicates, and quality flags.

#### Scenario: Re-indexing an unchanged document is idempotent
- **WHEN** `build_index` receives the same document and chunk schema twice
- **THEN** the second call reports a skip or unchanged status and does not duplicate chunk or FTS rows
