## ADDED Requirements

### Requirement: Source-aware scanned-page classification

The converter SHALL keep PDF-native text counts separate from Docling layout/OCR text counts and SHALL classify image-backed pages with insufficient native text but usable Docling text as scanned rather than native.

#### Scenario: Pure-image page receives Docling text

- **WHEN** PyMuPDF reports insufficient native text, the page contains image content, and Docling returns usable text
- **THEN** the page SHALL be reported as scanned with separate native and Docling character counts

### Requirement: Scan-dominant cell matching policy

The converter SHALL disable Docling PDF-cell matching for scan-dominant documents while retaining table-structure extraction.

#### Scenario: Most pages are scanned

- **WHEN** at least 80 percent of preflight pages are classified as scanned
- **THEN** Docling SHALL run with table structure enabled and PDF-cell matching disabled

#### Scenario: Repeated scan-like page geometry dominates

- **WHEN** most pages carry nearly identical high-density drawing geometry characteristic of a converted scan template
- **THEN** Docling SHALL run with PDF-cell matching disabled even if a PDF text layer is present

#### Scenario: Document is not scan-dominant

- **WHEN** fewer than 80 percent of pages are classified as scanned
- **THEN** Docling SHALL retain its normal PDF-cell matching behavior

### Requirement: Local vector-grid candidates

The converter SHALL emit vector table candidates only for local regions that exhibit table-like horizontal/vertical grid topology.

#### Scenario: Image-dominant scan has decorative lines

- **WHEN** a page has insufficient native text and high image coverage
- **THEN** drawing count alone SHALL NOT create a vector table candidate

#### Scenario: Native page contains a grid

- **WHEN** a native page contains at least two horizontal lines, two vertical lines, and four intersections in a connected local region
- **THEN** the converter SHALL emit a candidate whose bbox encloses that region rather than the whole page

### Requirement: Candidate deduplication

The converter SHALL prioritize structured table sources and SHALL not retain an overlapping empty vector candidate as a separate top-level table.

#### Scenario: Vector candidate overlaps Docling table

- **WHEN** a vector candidate without valid HTML overlaps a structured Docling table on the same page
- **THEN** the structured table SHALL be retained and the candidate SHALL be recorded only as an alternative source

### Requirement: Honest table quality reporting

The quality report SHALL expose structured, candidate, valid, invalid, and needs-review table counts and SHALL include table quality in the overall score.

#### Scenario: Most table records are unresolved candidates

- **WHEN** a conversion contains many table records without valid HTML or marked for review
- **THEN** `quality_score` SHALL be below 1.0 and `needs_review` SHALL be true

### Requirement: Optional backend short-circuit

The converter SHALL not load or invoke optional table backends when no eligible page numbers are provided.

#### Scenario: No vector table page survives filtering

- **WHEN** the eligible table-page set is empty
- **THEN** Camelot and pdfplumber SHALL not be imported or called for that conversion
