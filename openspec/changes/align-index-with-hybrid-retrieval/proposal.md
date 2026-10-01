## Why

The retrieval stage now combines SQLite FTS5/BM25 with a local vector sidecar
and fuses candidates by chunk id. The index must expose an explicit, stable
retrieval corpus contract so both channels use exactly the same retrieval
children and text hashes, including after a document rebuild.

## What Changes

- Add a document-level retrieval-corpus fingerprint and counts to the index
  manifest and `pipeline_documents` metadata.
- Add a covering index for retrieval-child ordering and make the FTS contract
  explicit in the manifest.
- Preserve `content_hash`/`text_search` as the shared BM25/vector identity;
  parents remain excluded from both candidate channels.
- Run BM25 and vector candidate channels for every configured hybrid query,
  merge by chunk id, and rank with configurable weighted RRF.
- Expose BM25/vector ranks, raw scores, fusion contribution, active channels,
  and explicit lexical fallback diagnostics.
- Keep the existing `build_index` API and lexical/semantic fallback behavior.

## Non-goals

- Do not change embedding model selection or vector storage.
- Do not change PDF conversion, chunking, reranking, or GUI integration.

## Impact

`knowledge_pipeline/index.py`, `embed.py`, `semantic_retrieve.py`,
`retrieve.py`, and focused tests are changed. Existing SQLite pipeline
databases migrate additively and rebuild once when the index stage fingerprint
changes.
