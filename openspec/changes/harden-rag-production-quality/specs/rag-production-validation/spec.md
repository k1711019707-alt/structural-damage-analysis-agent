## MODIFIED Requirements

### Requirement: Validation SHALL assess corpus quality and expected retrieval
Validation SHALL report structural health, manifest integrity, schema compatibility, source-hash duplicates, page/OCR quality, selected-scope coverage, and representative query expectations.

#### Scenario: Representative query returns unrelated data
- **WHEN** a query returns chunks but none match its configured expected document or standard identifier
- **THEN** validation SHALL mark that query failed rather than treating non-zero chunks as success.

#### Scenario: Duplicate sources
- **WHEN** multiple indexed documents share one source SHA-256
- **THEN** validation SHALL report the duplicate group and SHALL fail a no-duplicate required gate.

#### Scenario: Validation succeeds
- **WHEN** all required integrity, schema, quality, duplicate, scope, and query checks pass
- **THEN** the candidate SHALL be reported ready for a separate activation operation.
