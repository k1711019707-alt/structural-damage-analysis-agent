## ADDED Requirements

### Requirement: Restore the complete conversion contract
The chunk stage MUST restore blocks, pages, images, tables, quality report, status,
metadata, and schema fields when reading a conversion JSON payload.

#### Scenario: CLI preserves table and quality evidence
- **WHEN** a conversion JSON contains blocks, tables, images, pages, and a quality report
- **THEN** chunking MUST be able to use and preserve those records in emitted metadata

### Requirement: Select and deduplicate primary text
The chunk stage MUST select at most one primary page text stream using the precedence
Docling layout, native PyMuPDF, OCR, then document projection, while retaining useful
supplemental evidence and suppressing exact normalized duplicates.

#### Scenario: Docling page blocks are available
- **WHEN** a page contains `layout:docling` blocks
- **THEN** those blocks MUST be the page primary stream and equivalent native/OCR text MUST NOT create duplicate primary chunks

#### Scenario: Only projection is available
- **WHEN** no page-level primary blocks exist but a `document_projection` block exists
- **THEN** the projection MAY be chunked as fallback and MUST be emitted at most once

### Requirement: Preserve structural boundaries
The chunk stage MUST identify or preserve heading paths, clause numbers, list markers,
page numbers, block IDs, and cross-page continuation relationships before applying size limits.

#### Scenario: Clause spans pages
- **WHEN** consecutive page blocks belong to one clause and no new heading or clause starts
- **THEN** the parent unit MUST combine them, list all source pages, and mark the unit as cross-page

#### Scenario: Independent clauses are adjacent
- **WHEN** a new clause number is detected
- **THEN** the chunker MUST NOT merge it into the preceding clause merely to fill a size target

### Requirement: Emit parent and retrieval child chunks
The chunk stage MUST emit context parents and retrieval children with `parent_id`,
`chunk_level`, `retrieval_role`, contextualized text, and source markers. Default child
size MUST be 520 tokens with an 80-token overlap target and default parent size MUST be 1600 tokens.

#### Scenario: Short clause
- **WHEN** a clause fits within the child limit
- **THEN** the clause MUST remain intact as a retrieval child linked to its parent

#### Scenario: Long clause
- **WHEN** a structural unit exceeds the child limit
- **THEN** it MUST be split at semantic/token boundaries before character fallback, without losing heading or clause context

### Requirement: Handle tables as structured evidence
The chunk stage MUST canonicalize duplicate table records, exclude empty candidates from searchable text,
and emit a complete table parent plus row children whose text repeats the table header.

#### Scenario: Usable table with rows
- **WHEN** a table has non-empty rows or markdown
- **THEN** every row child MUST include table identity, page, heading context, and the header

#### Scenario: Empty table candidate
- **WHEN** a candidate has no rows and no markdown
- **THEN** it MUST produce only a diagnostic/reference record marked for review and MUST NOT create empty FTS text

### Requirement: Preserve image/OCR provenance
Image or OCR chunks MUST only be searchable when usable text exists and MUST retain image/page,
confidence, extraction method, and review flags.

#### Scenario: Image has OCR text
- **WHEN** an image record or OCR block contains usable text
- **THEN** an image/OCR chunk MAY be emitted with its image ID and source page

#### Scenario: Image has no text
- **WHEN** an image has no usable OCR or caption text
- **THEN** the chunk stage MUST preserve a non-searchable reference in metadata and MUST NOT emit an empty text chunk
