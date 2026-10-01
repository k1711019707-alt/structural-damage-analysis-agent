## 1. Index schema and ingestion

- [x] 1.1 Define versioned v2 SQLite tables for documents, pages, chunks, relations, tables, images, and FTS.
- [x] 1.2 Implement metadata normalization, standard/clause extraction, quality flags, content hashes, and duplicate groups.
- [x] 1.3 Implement transactional single-document replacement, unchanged-source skip, and batch-safe SQLite pragmas.
- [x] 1.4 Emit manifest counts and schema/source fingerprints without overwriting legacy databases.

## 2. Retrieval compatibility

- [x] 2.1 Filter primary lexical/LIKE retrieval to retrieval-role chunks and keep scoped fallback isolated.
- [x] 2.2 Hydrate one parent context per child while preserving max-k and max-char behavior.
- [x] 2.3 Return persisted metadata and structural scores while retaining legacy adapter behavior.

## 3. Verification

- [x] 3.1 Add tests for metadata preservation, parent exclusion/hydration, tables, images, quality flags, and scope isolation.
- [x] 3.2 Add idempotent/incremental indexing tests and manifest counter assertions.
- [x] 3.3 Run focused pipeline tests and OpenSpec validation.
