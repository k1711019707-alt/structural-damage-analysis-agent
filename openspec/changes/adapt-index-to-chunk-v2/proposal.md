## Why

The v2 chunker now emits visual-region evidence and richer table/image
metadata. The index already separates parent context from retrieval children,
but it does not persist visual regions as first-class evidence and overwrites
the table/image registry with whichever child happens to be processed last.

## What Changes

- Persist visual-region records in a dedicated SQLite table while indexing
  their searchable OCR/vision text only through retrieval children.
- Persist non-searchable `image_references` and `table_diagnostics` without
  adding empty or speculative text to FTS.
- Keep table/image registry rows anchored to the parent when available and
  retain child ids in a relation table, avoiding last-child overwrite.
- Normalize visual metadata and expose visual-region counts in the manifest.
- Preserve child-only FTS, parent hydration, idempotent replacement, and the
  existing `build_index` API.

## Non-goals

- Do not change PDF conversion, chunking, retrieval ranking, or add embeddings.
- Do not index context-only parents in FTS5.

## Impact

Only `knowledge_pipeline/index.py` and focused pipeline tests are changed. The
SQLite schema is additive and can migrate existing pipeline databases in place.
