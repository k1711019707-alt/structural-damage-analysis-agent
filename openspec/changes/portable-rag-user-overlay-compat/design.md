## Context

`active_rag_manifest_path()` intentionally gives a frozen user activation precedence over the bundled read-only baseline. This is correct for user-created RAGs, but a previous installation can leave a healthy overlay whose document IDs do not match the selected IDs persisted by the current GUI profile. The current scoped retrieval path raises before trying the bundled snapshot.

## Decision

Make active-RAG status inspection accept an explicit manifest path. In scoped retrieval, when the selected user active-v2 database cannot provide one or more requested IDs, inspect the bundled manifest in frozen mode. If its strict health gate passes and all requested IDs are present in its `pipeline_chunks`, switch the retrieval operation to that bundled state. Otherwise preserve the existing explicit error.

Semantic retrievers are created and cache-bound to the selected manifest/database/index fingerprints, so switching state cannot reuse a user-overlay semantic array for the bundled database. The fallback is read-only and does not alter `active_rag.json`, user settings, or the production knowledge directory.

## Verification

- Unit-test a healthy user overlay with a bundled scope and assert bundled fallback.
- Unit-test that an incomplete bundled scope still raises the original fail-closed error.
- Run focused runtime/packaging tests and strict OpenSpec validation.
- Build a fresh versioned portable candidate and run the extracted verifier plus an isolated report-RAG smoke query.
