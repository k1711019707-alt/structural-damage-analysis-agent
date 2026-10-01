## 1. Contracts and preflight

- [x] 1.1 Add page preflight fields and v3 visual/table fields.
- [x] 1.2 Implement deterministic feature collection and page classification.

## 2. Table and provenance

- [x] 2.1 Implement safe HTML generation, normalization, validation and legacy rows/markdown conversion.
- [x] 2.2 Route eligible table pages and deduplicate backend results with conflict metadata.
- [x] 2.3 Preserve raw/OCR/vision layers and source locations.

## 3. Optional visual enhancement

- [x] 3.1 Add injected vision adapter/options with redacted warnings and bounded region calls.
- [x] 3.2 Emit visual regions and quality diagnostics without blocking deterministic conversion.

## 4. Verification

- [x] 4.1 Existing focused PDF tests remain green; implementation adds v3 routing/HTML/vision paths.
- [x] 4.2 Run focused tests, relevant stage tests and strict OpenSpec validation.
