## 1. Root-cause tests

- [x] 1.1 Add failing tests for image-dominant drawing suppression and local vector-grid bbox detection.
- [x] 1.2 Add failing tests for scanned Docling source classification and scan-dominant cell-matching selection.
- [x] 1.3 Add failing tests for overlapping candidate deduplication, optional-backend short-circuit, and table quality scoring.

## 2. Implementation

- [x] 2.1 Implement source-aware page metrics and scanned routing.
- [x] 2.2 Implement scan-dominant Docling cell-matching configuration.
- [x] 2.3 Replace drawing-count candidates with local vector-grid detection.
- [x] 2.4 Implement structured-source candidate deduplication and optional-backend short-circuit.
- [x] 2.5 Add table-quality metrics and scoring.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation and focused unit tests.
- [x] 3.2 Re-run the 85-page scan conversion and compare page/table/warning metrics.
- [x] 3.3 Re-run chunk, index, and retrieve launchers against the new conversion.
- [x] 3.4 Run the broader PDF and knowledge-pipeline regression tests.
