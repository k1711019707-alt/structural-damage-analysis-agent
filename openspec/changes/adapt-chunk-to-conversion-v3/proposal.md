## Why

`pdf_convert.py` now emits the `knowledge-conversion.v3` contract. The chunk
stage still assumes the previous table shape (`rows`/`markdown`) and does not
consume the new `visual_regions` or layered image fields, so converted tables
can disappear or fail during chunking and visual evidence is not searchable.

## What Changes

- Restore `visual_regions` and v3 metadata in `conversion_from_payload`.
- Parse canonical table HTML into deterministic rows for table parent/child
  chunks, while retaining a read-only compatibility path for legacy payloads.
- Preserve table metadata, HTML validity, conflicts, and review flags in chunk
  metadata without treating HTML markup as searchable text.
- Emit searchable visual-region chunks only when OCR or vision-search text is
  present; keep empty regions as review references.
- Add regression coverage for v3 tables, visual regions, and legacy payloads.

## Non-goals

- Do not change PDF extraction decisions or downstream index/retrieval modules.
- Do not promote model-generated visual summaries to authoritative source text.

## Impact

Only `knowledge_pipeline/chunk.py` and focused tests are changed. The chunk
output remains `knowledge-chunks.v2` and keeps the existing `ChunkRecord` outer
shape; new facts remain in `metadata`.
