## Design

1. Treat `TableRecord.html` as the canonical table body. Use Python's standard
   HTML parser to extract `tr`/`th`/`td` cells, normalize whitespace, and pad
   rows to a stable column count. Legacy `rows`/`markdown` values are accepted
   only while reading old JSON and are converted to the same internal rows.
2. Keep table parent/row-child semantics unchanged. Repeat the first row as a
   header and carry `table_id`, `extraction_method`, `bbox`, `needs_review`,
   `html_valid`, and quality flags into metadata.
3. Map visual regions to `image_ocr`-compatible retrieval chunks. Search text
   is `ocr_text` plus `vision_search`/`vision_summary`, while metadata retains
   `vision_raw`, `region_type`, model-generated flags, and review status.
   Regions without usable text become `visual_references` diagnostics.
4. Preserve provenance markers and page numbers for every emitted chunk.

## Compatibility

The adapter accepts v3 payloads and old v2 fixtures. It does not mutate the
conversion JSON or alter `pdf_convert.py`.
