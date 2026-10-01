## 1. Regression Tests

- [x] 1.1 Add a failing table regression for same-page tables with a shared long header and different indicator rows.
- [x] 1.2 Add duplicate-provenance tests for exact and conflicting cross-backend table detections.
- [x] 1.3 Add semantic CLI contract tests for hit, no-hit, scope fields, and candidate compatibility.
- [x] 1.4 Add rerank and generation tests for scope and warning propagation.

## 2. Table Deduplication

- [x] 2.1 Replace fixed-prefix matching with full normalized matrix identity and conservative cross-backend equivalence.
- [x] 2.2 Preserve merged-source provenance and retain materially conflicting tables.

## 3. Retrieval Contract

- [x] 3.1 Add the versioned standalone semantic retrieval result contract and serialization helper.
- [x] 3.2 Add scope fields to rerank and generation contracts and propagate them through both builders.

## 4. Verification

- [x] 4.1 Run focused regressions and broader knowledge-pipeline tests with the project Conda interpreter.
- [x] 4.2 Re-run the current PDF-derived chunk and index stages and confirm all 61 source table IDs are accounted for.
- [x] 4.3 Re-run semantic, scoped hybrid retrieval, rerank, and generation launchers and verify the unified metadata contract.
- [x] 4.4 Verify SQLite integrity, retrieval/vector fingerprint parity, and unchanged `pending_engineer_review` behavior.
