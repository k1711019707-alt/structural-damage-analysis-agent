## ADDED Requirements

### Requirement: Scope-safe child retrieval with context hydration
The retrieval adapter SHALL filter primary results to `retrieval_role=retrieval`, honor document scope, preserve chunk metadata and provenance, and may add the matching parent context without allowing parent rows to consume the requested child top-k.

#### Scenario: Scoped query returns child evidence and parent context
- **WHEN** a query matches a child in one requested document
- **THEN** results contain that child with its metadata/source marker and at most one same-document parent context

### Requirement: Specialized structural matching
The retrieval adapter SHALL support exact or high-weight lexical matching for normalized standard numbers, clause numbers, headings, table rows, and image OCR while retaining LIKE/scoped fallback behavior.

#### Scenario: Clause query finds a table or clause child
- **WHEN** the query contains a clause number or table term
- **THEN** the adapter can return the corresponding retrieval child within the requested document scope
