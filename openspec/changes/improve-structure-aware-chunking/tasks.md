## 1. Contracts and minimal converter fixes

- [x] 1.1 Add complete conversion-payload restoration helpers for all nested records
- [x] 1.2 Fix duplicate `document_projection` and duplicate Docling warning insertion
- [ ] 1.3 Add stable source-role/order metadata and document-scoped Docling table IDs where needed
- [x] 1.4 Add and lock the explicit `semchunk` dependency

## 2. Structure-aware chunk core

- [x] 2.1 Add chunk configuration, token counter, and deterministic fallback splitter
- [x] 2.2 Implement primary-source selection, normalization, boilerplate filtering, and deduplication
- [x] 2.3 Implement heading/clause/list detection and cross-page structural units
- [x] 2.4 Implement contextualized text and provenance metadata construction
- [x] 2.5 Implement parent/child generation and semantic splitting

## 3. Specialized evidence

- [x] 3.1 Implement table canonicalization, alternatives/conflict flags, and header repetition
- [x] 3.2 Implement image/OCR chunk handling and non-searchable references
- [x] 3.3 Add chunk validation diagnostics and v2 payload metadata

## 4. Verification and compatibility

- [ ] 4.1 Extend stage tests for clauses, cross-page text, tables, OCR, duplicates, and parent links
- [x] 4.2 Preserve legacy `size`/`overlap` API and CLI compatibility as fallback aliases
- [x] 4.3 Run focused tests, CLI smoke tests, and OpenSpec validation
- [x] 4.4 Update pipeline README with v2 chunk behavior and migration notes
