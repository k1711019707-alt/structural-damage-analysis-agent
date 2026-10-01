## 1. OCR and ingestion quality

- [x] 1.1 Normalize RapidOCR NumPy/list/dict result shapes without array truth-value evaluation.
- [x] 1.2 Classify unrecovered OCR pages as failed, deduplicate quality page lists, and add regression tests.
- [x] 1.3 Propagate source metadata, quality score, page warnings, conversion warnings, and page coverage through chunk and index contracts.

## 2. Production corpus construction

- [x] 2.1 Deduplicate source files by SHA-256 while preserving aliases and canonical-source evidence.
- [x] 2.2 Record conversion page coverage and quality metrics in the build manifest and fail required quality gates.
- [x] 2.3 Preserve current active/legacy databases and build only into a new versioned candidate directory.

## 3. Activation and runtime integrity

- [x] 3.1 Enforce current required tables/columns, FTS parity, document metadata coverage, and actual database SHA checks.
- [x] 3.2 Resolve and validate optional semantic sidecar paths relative to the user knowledge root.
- [x] 3.3 Fall back to the same selected legacy scope when selected IDs are absent from v2 and expose backend/fallback diagnostics.
- [x] 3.4 Close SQLite WAL, runtime cache, and semantic validate/load integrity gaps with adversarial regression tests.
- [x] 3.5 Bound semantic companion manifests and reject unsupported or non-finite vector arrays in both health and retrieval paths.

## 4. Validation and operations

- [x] 4.1 Extend validation with expected-document queries, duplicate SHA reporting, schema/quality checks, and non-zero relevance assertions.
- [x] 4.2 Add focused regression tests and run the focused RAG regression suite; the unrelated adaptive-layout failure remains outside this change.
- [x] 4.3 Update the production runbook and strictly validate this OpenSpec change.
- [x] 4.4 Build and validate a new candidate, compare it with the active version, and retain rollback evidence before activation; activation remains intentionally blocked by failed quality gates.
- [x] 4.5 Add exact source-title/standard retrieval seeding and make all representative query expectations pass without scope broadening.
- [x] 4.6 Add GUI-Responses-API-only blank-page adjudication with strict schema, fail-closed fallback, page-inventory retention, and regression tests.
