## ADDED Requirements

### Requirement: OCR normalization SHALL support array-shaped backend results
The converter SHALL normalize supported OCR result shapes without evaluating NumPy arrays or equivalent multi-element containers as booleans.

#### Scenario: NumPy OCR output
- **WHEN** the OCR backend exposes boxes, texts, or scores as NumPy arrays
- **THEN** conversion SHALL emit normalized OCR records without an ambiguous truth-value exception.

### Requirement: Unrecovered pages SHALL remain visible as failed evidence
The converter SHALL classify a page as failed when it requires OCR and neither native extraction nor OCR yields usable text, and SHALL retain a page-level warning and unique failed-page identifier.

#### Scenario: OCR failure on a scanned page
- **WHEN** OCR raises an exception or yields no usable text on a page without usable native text
- **THEN** the quality report SHALL contain that page exactly once in `failed_pages` and SHALL not silently represent the document as fully covered.

### Requirement: Quality diagnostics SHALL propagate into the production index
The chunk and index stages SHALL preserve source identity, document quality score, page coverage, page warnings, and conversion warnings required for activation decisions.

#### Scenario: Index an incomplete document
- **WHEN** a converted document contains failed or low-quality pages
- **THEN** the indexed document and page inventory SHALL expose those facts to validation and health checks.

### Requirement: Candidate blank pages SHALL use the configured GUI Responses API for adjudication
The converter SHALL send only deterministic `blank_or_unreadable` candidates to the existing GUI-configured remote Responses API, require a strict JSON classification of `blank`, `non_blank`, or `uncertain`, and fail closed to OCR/quality handling when the API is unavailable or the response is invalid. A high-confidence `blank` result SHALL skip OCR and chunk generation while retaining the page inventory and auditable decision evidence.

#### Scenario: Remote API confirms a blank page
- **WHEN** deterministic preflight finds no usable text or visual content and the configured GUI Responses API returns valid high-confidence `blank`
- **THEN** the page SHALL be marked as intentional blank, SHALL remain in page inventory, and SHALL not generate OCR output or retrieval chunks.

#### Scenario: Remote blank adjudication is unavailable
- **WHEN** the configured API times out, lacks credentials, returns malformed JSON, or returns `uncertain`
- **THEN** the converter SHALL not auto-exempt the page and SHALL continue the existing OCR/failed-page quality path.

### Requirement: Production build SHALL deduplicate identical source content
The build SHALL index one canonical document per source SHA-256 and SHALL record alternate file names and paths as aliases.

#### Scenario: Same PDF under two names
- **WHEN** two managed files have the same SHA-256
- **THEN** the candidate corpus SHALL contain one indexed document and the build manifest SHALL list both source aliases.
