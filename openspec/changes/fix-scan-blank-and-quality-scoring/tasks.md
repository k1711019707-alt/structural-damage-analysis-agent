## 1. Root-cause regression tests

- [x] 1.1 Add failing tests for a near-white scanned page with an isolated micro-speck and a false one-character Docling block.
- [x] 1.2 Add protection tests proving sparse meaningful text/graphics are not auto-suppressed.
- [x] 1.3 Add failing tests for distinct blank/noise/failed inventories and source-neutral scanned-page quality.
- [x] 1.4 Add scoring tests for effective-content denominators, genuine extraction failure, tables, and unresolved review diagnostics.

## 2. Detection and routing implementation

- [x] 2.1 Add local rendered-page foreground and connected-component analysis with conservative thresholds.
- [x] 2.2 Integrate `scan_noise_only` before OCR, table routing, visual enhancement, and block emission while retaining audit facts.
- [x] 2.3 Add compatible page and quality-report fields for foreground metrics, noise inventories, effective content pages, and OCR recovery.

## 3. Quality scoring implementation

- [x] 3.1 Stop treating scanned/mixed source types as automatically low quality or review-required.
- [x] 3.2 Rework quality finalization and scoring around effective content pages and actual diagnostic failures.
- [x] 3.3 Keep table and unresolved header-review penalties bounded and machine-readable.

## 4. Verification

- [x] 4.1 Run strict OpenSpec validation and focused PDF conversion unit tests.
- [x] 4.2 Run the broader knowledge-pipeline regression tests.
- [x] 4.3 Re-run the current 200-page conversion and verify page 14 has no blocks/tables/failure entry.
- [x] 4.4 Compare before/after quality metrics and validate the module launcher output.
