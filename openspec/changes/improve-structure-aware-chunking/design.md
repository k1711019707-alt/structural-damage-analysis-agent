## Context

`pdf_convert.py` now returns page-ordered `BlockRecord` values from Docling,
PyMuPDF, or RapidOCR, plus `TableRecord`, `ImageRecord`, page quality facts,
and an optional document projection. The existing chunker ignores all but block
text and uses character windows. The project must preserve `[KB:document_id:location]`
provenance, scope isolation, JSON inspectability, and deterministic fallbacks.

## Goals / Non-Goals

**Goals:**

- Produce stable, inspectable chunks aligned to headings, clauses, lists,
  tables, figures/OCR, and page continuations.
- Keep raw text and evidence metadata while adding searchable contextual text.
- Use token-aware semantic splitting without requiring a network download.
- Provide parent context and child retrieval records without breaking the outer
  JSON/API contract.
- Avoid duplicate primary text, duplicate tables, and duplicate projection data.

**Non-Goals:**

- Do not replace PDF extraction or introduce a full DoclingDocument persistence
  format.
- Do not implement embeddings, hybrid retrieval, reranking, or query expansion.
- Do not silently discard low-quality or conflicting evidence; mark it.

## Decisions

1. **Use a project adapter instead of direct `HybridChunker`.** The input is a
   custom JSON contract, not a live `DoclingDocument`; direct use would require
   re-conversion or a new persisted format. Reuse Docling's hierarchy ideas and
   `semchunk`'s token-aware splitting in project code.
2. **Primary-source precedence is layout Docling > native PyMuPDF > OCR >
   document projection.** OCR is supplemental when native text exists; projection
   is fallback-only. Identical normalized text is emitted once.
3. **Structure before size.** Build units by heading/clause/list/paragraph and
   merge only compatible adjacent units. Split an overlong unit by semchunk,
   then sentence boundaries, then character windows.
4. **Parent/child records are explicit.** Parents use `retrieval_role=context_only`;
   children use `retrieval_role=retrieval`, both retain `parent_id` and source
   metadata. Defaults are child 520 tokens, 80-token overlap, parent 1600 tokens.
5. **Tables are type-specific.** Canonicalize records by page/bbox/content,
   prefer non-empty Docling then Camelot then pdfplumber, retain alternatives and
   conflicts, emit a full parent and header-repeated row children. Empty
   candidates become diagnostics only.
6. **Compatibility stays in metadata.** Keep `ChunkRecord` fields unchanged and
   place new fields under `metadata`; bump only the version marker.
7. **PDF changes are narrowly scoped.** Remove duplicate projection/warning
   insertion and add stable source metadata/IDs only where useful to chunking.

## Risks / Trade-offs

- [Risk] Heuristic heading/clause detection can misclassify unusual standards.
  → Preserve raw blocks, emit detection flags, and test representative fixtures.
- [Risk] Multiple table backends may disagree. → Keep alternatives/conflict flags
  and never merge conflicting cell text silently.
- [Risk] `semchunk` or tokenizer may be unavailable/offline. → Use lazy optional
  import and deterministic fallback counters/splitting.
- [Risk] Index currently indexes every emitted record. → Mark retrieval roles now;
  later index/retrieve changes can filter children and hydrate parents.

## Migration Plan

1. Apply the minimal PDF duplicate fixes and add `semchunk` to dependency locks.
2. Implement the v2 chunk adapter and validators while retaining old function
   arguments as compatibility aliases.
3. Add unit/CLI/fixture tests and regenerate chunks before rebuilding indexes.
4. Roll back by using the previous chunk module and v1 generated JSON; source
   conversion files remain readable because the converter contract is retained.

## Open Questions

- Should `index.py` be updated in a later change to index only retrieval children
  and hydrate parents at retrieval time?
- Which local tokenizer should production use for the final embedding model?
