## ADDED Requirements

### Requirement: Page-level deterministic preflight
The converter SHALL collect page number, dimensions, rotation, native text counts, image count/area ratio, drawing count, text density, encoding anomaly and layout complexity before routing enhancement work.

#### Scenario: Native page
- **WHEN** a page has sufficient native text and low visual complexity
- **THEN** it is classified as `native_text`, Docling/native facts are retained, and OCR/vision is not required.

#### Scenario: Scanned page
- **WHEN** a page has insufficient native text and contains image content
- **THEN** it is classified as `scanned`, OCR is attempted, and OCR boxes/confidence are retained when available.

### Requirement: Safe table HTML
The converter SHALL expose table HTML as the canonical table body, escape cell text, preserve empty cells and available rowspan/colspan, and reject unsafe tags.

#### Scenario: Backend table
- **WHEN** Docling, Camelot, or pdfplumber returns table cells
- **THEN** the result is normalized to safe HTML with extraction method and quality metadata.

#### Scenario: Conflicting backends
- **WHEN** multiple backends produce the same page table
- **THEN** normalized-content deduplication retains one primary result and records alternatives/conflicts in metadata.

### Requirement: Optional vision enhancement
The converter SHALL support optional page/region vision enhancement with model/source metadata, and SHALL continue deterministic conversion when vision configuration or invocation is unavailable.

#### Scenario: Vision unavailable
- **WHEN** the key, endpoint, model, dependency or call is unavailable
- **THEN** a redacted warning is recorded and no raw secret is persisted.

### Requirement: Layered provenance
The converter SHALL keep raw/native text, OCR text and vision summaries in separate fields with page/bbox/source provenance.

#### Scenario: Figure region
- **WHEN** a figure or complex visual region is enhanced
- **THEN** OCR text and vision summary are emitted separately and marked as model-derived where applicable.
