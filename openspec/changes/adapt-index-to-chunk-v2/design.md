## Design

Add `pipeline_visual_regions` keyed by `(document_id, region_id)`, with page,
bbox, region type, OCR, vision search/summary/raw fields, model-generated and
review flags, plus retained metadata JSON. Add `pipeline_evidence_links` for
many-to-one table/image/visual-to-chunk links where a parent and several
retrieval children represent the same evidence item.

During indexing, write every chunk as before, write retrieval children to FTS,
then upsert table/image/visual evidence using the parent id when it exists and
the current child id as a fallback. Registry rows no longer depend on child
iteration order. Manifest counters distinguish table chunks, image OCR chunks,
visual-region chunks, and non-searchable diagnostic references. Top-level
`image_references` and `table_diagnostics` participate in the document
fingerprint and evidence registries but never enter FTS.
