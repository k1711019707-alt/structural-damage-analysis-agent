## Why

The current chunk stage applies a fixed character window to every block and
discards tables, images, pages, quality reports, and most layout context when
invoked from JSON. This causes clause and cross-page fragmentation, duplicate
Docling/OCR content, weak table retrieval, and loss of source evidence. The
latest `pdf_convert.py` already exposes enough structured evidence to improve
chunk quality without replacing its extraction pipeline.

## What Changes

- Upgrade `chunk.py` to fully restore the `DocumentConversion` JSON contract.
- Select one page-level primary text source (Docling layout, PyMuPDF native,
  OCR, or projection fallback) and deduplicate supplemental OCR/projection
  content.
- Detect headings, clauses, lists, and cross-page continuations while retaining
  page, bbox, block, extraction, and quality provenance.
- Add token-aware semantic splitting using `semchunk` when available, with a
  deterministic sentence/character fallback.
- Emit parent context chunks and retrieval child chunks with explicit metadata
  and contextualized searchable text.
- Normalize and deduplicate Docling/Camelot/pdfplumber/candidate tables; emit
  complete table parents and row children with repeated headers.
- Emit image/OCR chunks only when usable text exists; preserve text-free image
  references as metadata rather than empty searchable records.
- Add chunk validation diagnostics and regression tests for the new behavior.
- Fix duplicate `document_projection` and duplicate Docling warning insertion in
  `pdf_convert.py`; do not otherwise change extraction decisions.
- **BREAKING**: bump the chunk output schema/stage version to `knowledge-chunks.v2`/
  `chunk.v2`; retain the existing `ChunkRecord` outer shape for consumers.

## Capabilities

### New Capabilities

- `structure-aware-chunking`: Build provenance-preserving, structure-aware
  parent/child chunks from `DocumentConversion`.
- `chunk-quality-validation`: Report chunk integrity, source, duplication, and
  type-specific quality diagnostics.

### Modified Capabilities

None.

## Impact

Affected files are `knowledge_pipeline/chunk.py`, selected duplicate-handling
lines in `knowledge_pipeline/pdf_convert.py`, dependency manifests for the
explicit `semchunk` runtime dependency, and knowledge-pipeline tests. The
current `index.py` can ingest the output unchanged; parent/child retrieval-role
metadata is emitted for subsequent index/retrieve filtering.
