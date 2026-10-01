## ADDED Requirements

### Requirement: Local review of structured page headers

The PDF converter SHALL review only Docling page-header blocks that have a usable PDF bounding box, using the existing local OCR adapter and a rendered page image, while leaving pages without such candidates on the existing conversion path.

#### Scenario: Header candidate is present

- **WHEN** a converted page contains one or more blocks whose `metadata.docling_label` is `page_header` and whose `bbox` is available
- **THEN** the converter SHALL render and OCR that page at most once for the header-review stage and SHALL attempt positional matching of OCR results to those blocks

#### Scenario: No header candidate is present

- **WHEN** a converted page contains no eligible page-header block
- **THEN** the converter SHALL NOT run the header-review OCR stage for that page

### Requirement: Conservative correction and auditability

The converter SHALL replace a page-header block's text projections only when the matched local OCR result is non-empty, meets the configured confidence threshold, passes positional matching, and contains plausible text; every attempted review SHALL remain auditable in block metadata.

#### Scenario: High-confidence recovery

- **WHEN** a matched OCR result for `建材发展导白` is `建材发展导向` with confidence at least the configured threshold and an overlapping location
- **THEN** the block SHALL expose `建材发展导向` through its text, display-text, and search-text projections, while preserving `建材发展导白` as `metadata.text_raw` and recording a `header_review.status` of `recovered`

#### Scenario: Unsafe candidate

- **WHEN** OCR confidence, text plausibility, or positional matching is insufficient
- **THEN** the converter SHALL preserve the original projections and SHALL record `header_review.status` as `needs_review` (or an equivalent non-recovered status)

### Requirement: Quality metrics

The conversion quality output SHALL expose counts for header reviews and their outcomes, without claiming that the whole page or document was OCR-successful.

#### Scenario: Quality report is emitted

- **WHEN** conversion completes after header candidates were reviewed
- **THEN** the quality output SHALL include review count, recovered count, confirmed count, needs-review count, and the local review method

### Requirement: Downstream compatibility

The corrected header text SHALL remain available to existing chunking, indexing, and retrieval consumers without changing their public contracts.

#### Scenario: Corrected conversion is chunked and indexed

- **WHEN** the resulting conversion is passed to the existing chunk and index modules
- **THEN** those modules SHALL consume the corrected text as ordinary block content and preserve existing document/page provenance
