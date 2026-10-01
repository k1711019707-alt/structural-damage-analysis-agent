## ADDED Requirements

### Requirement: v3 table adaptation
The chunk stage SHALL use `TableRecord.html` as the canonical table source,
extract non-empty table rows deterministically, and preserve table provenance
and review metadata in emitted parent and child chunks.

#### Scenario: HTML table is converted
- **WHEN** a conversion payload contains a valid table HTML string
- **THEN** chunking emits a complete table parent and header-repeated row child
  chunks with the table id, source method, page, and review flags.

### Requirement: visual region adaptation
The chunk stage SHALL expose searchable visual-region text only when OCR or
vision-search content is present and SHALL retain empty regions as review
references rather than empty chunks.

#### Scenario: visual region has OCR or search text
- **WHEN** a region contains `ocr_text` or `vision_search`
- **THEN** chunking emits a retrieval child with page provenance and layered
  visual metadata.
