## ADDED Requirements

### Requirement: Query type routing
The retriever MUST classify a query from query text alone and MUST select bounded lexical, semantic, exact-match, title-prior, and hierarchy policies without using benchmark labels.

#### Scenario: Route an exact identifier query
- **WHEN** a query contains a standard number or clause identifier
- **THEN** the route prioritizes exact and lexical evidence and retains semantic retrieval only as a supplement.

#### Scenario: Route a semantic paraphrase
- **WHEN** a natural-language query lacks exact identifiers, table markers, and numeric-unit anchors
- **THEN** the route assigns a stronger semantic contribution while retaining a lexical reserve.

### Requirement: Reserved hybrid candidate union
The retriever MUST preserve bounded candidates from lexical, semantic, and exact-match channels before fusion and final truncation.

#### Scenario: Preserve a lexical candidate
- **WHEN** a direct relevant chunk is inside the configured lexical reserve but would fall outside a single mixed RRF cutoff
- **THEN** the chunk remains in the union candidate pool available to reranking and final Top-K selection.

### Requirement: Weak source-title prior
The retriever MUST treat source-title matching as a document-routing or weak-prior signal and MUST NOT apply a large title score to every chunk in an explicitly scoped document.

#### Scenario: Search inside an explicit scope
- **WHEN** `document_ids` explicitly selects a document whose title matches the query
- **THEN** body chunks are ranked by their own evidence signals without a document-wide high title bonus.

### Requirement: Gated hierarchical context
The retriever MUST separate direct anchor ranking from hierarchy expansion and MUST support off, auto, and forced hierarchy policies with bounded context.

#### Scenario: Avoid expansion for a simple exact query
- **WHEN** auto policy receives a simple exact identifier, single numeric value, or simple table lookup with sufficient direct anchors
- **THEN** it returns direct anchors without unconditional parent or same-heading expansion.

#### Scenario: Expand a multi-evidence risk query
- **WHEN** auto policy receives a risk/condition query or direct anchors are insufficient and reliable hierarchy metadata exists
- **THEN** it adds bounded parent or same-heading context while keeping direct evidence identity separate.
