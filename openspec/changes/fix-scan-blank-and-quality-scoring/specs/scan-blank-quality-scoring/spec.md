## ADDED Requirements

### Requirement: Deterministic scan-noise page detection

The converter SHALL identify a page as `scan_noise_only` only when local pixel and connected-region evidence shows negligible isolated foreground content, extracted text is absent or limited to micro-block noise, and no valid table or meaningful graphic evidence exists.

#### Scenario: White scan contains one tiny central speck

- **WHEN** a low-text image-backed page contains only a tiny isolated dark component and an OCR or layout backend emits at most a micro-block character
- **THEN** the converter SHALL classify the page as `scan_noise_only`

#### Scenario: Sparse page contains a meaningful title or formula

- **WHEN** a low-text page contains a significant connected region or a non-micro text block
- **THEN** the converter SHALL NOT automatically classify the page as `scan_noise_only`

### Requirement: Scan-noise pages do not pollute retrieval content

The converter SHALL suppress false text and table candidates from confirmed `scan_noise_only` pages while retaining an audit explanation.

#### Scenario: Speck is recognized as the character one

- **WHEN** a confirmed `scan_noise_only` page has a layout block containing `1`
- **THEN** that block SHALL NOT be emitted in document blocks, chunks, or eligible table pages, and the suppressed text SHALL be recorded in `blank_review`

### Requirement: Blank and failed page inventories are distinct

The quality report SHALL keep intentional blank pages, locally confirmed scan-noise pages, low-quality content pages, and failed content pages as distinct inventories.

#### Scenario: Local detector confirms scan noise

- **WHEN** a page is classified as `scan_noise_only`
- **THEN** it SHALL appear in `scan_noise_only_pages` and SHALL NOT appear in `failed_pages` or `low_quality_pages`

### Requirement: Source-neutral page quality classification

The converter SHALL NOT mark a page low quality or requiring review solely because its source type is scanned or mixed.

#### Scenario: OCR successfully recovers a scanned page

- **WHEN** a scanned page produces usable text without extraction warnings or encoding anomalies
- **THEN** it SHALL count as a successfully recovered OCR page and SHALL NOT automatically enter `low_quality_pages`

### Requirement: Effective-content quality scoring

The overall quality score SHALL use effective content pages as the page-quality denominator and SHALL deduct only for actual failed pages, low-quality pages, unresolved table quality, or unresolved review diagnostics.

#### Scenario: Document contains one valid noise-only blank page

- **WHEN** a document has 199 successfully parsed content pages and one confirmed `scan_noise_only` page
- **THEN** the blank page SHALL not reduce the content-page success rate or quality score

#### Scenario: Content page genuinely fails extraction

- **WHEN** an effective content page has no usable native, layout, hidden, or OCR text and is not confirmed blank/noise-only
- **THEN** it SHALL reduce the quality score and appear in both `failed_pages` and `low_quality_pages`

### Requirement: Backward-compatible diagnostics

The converter SHALL add scan-noise and scoring metrics without removing existing conversion fields or changing the schema identifier.

#### Scenario: Existing v3 consumer reads conversion

- **WHEN** a consumer ignores unknown quality and page fields
- **THEN** it SHALL continue to read the existing `knowledge-conversion.v3` structure and prior fields
