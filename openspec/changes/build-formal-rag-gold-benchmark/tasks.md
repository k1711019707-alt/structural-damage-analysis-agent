## 1. Input inspection and schema

- [x] 1.1 Inspect the latest test SQLite schema and evidence distributions used by each query type.
- [x] 1.2 Define candidate, review, manifest, and Gold publication schemas with explicit non-Gold defaults.

## 2. Candidate generation

- [x] 2.1 Implement a deterministic read-only generator for the eight fixed type quotas.
- [x] 2.2 Add evidence/source resolution, candidate answers, required facts, forbidden errors, and provenance hashes.
- [x] 2.3 Write JSONL, CSV, Markdown, manifest, review template, and coverage report outputs under `benchmarks/rag/formal_v1/`.

## 3. Review and publication controls

- [x] 3.1 Implement structural/quota/evidence validation for automatic candidates.
- [x] 3.2 Implement a Gold publication gate that rejects incomplete original-PDF review records.
- [x] 3.3 Document the page-level human review and Gold promotion workflow.

## 4. Tests and delivery

- [x] 4.1 Add focused tests for deterministic quotas, traceability, non-Gold boundaries, and publication rejection/acceptance.
- [x] 4.2 Generate the 150-record candidate set from the latest test snapshot and inspect distributions/duplicate evidence.
- [x] 4.3 Run focused tests and strict OpenSpec validation, then record final evidence and known manual-review boundary.
