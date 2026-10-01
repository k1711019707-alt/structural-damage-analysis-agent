## ADDED Requirements

### Requirement: Human-attested Gold promotion
The system MUST preserve the user's explicit completed-review attestation when promoting the 150 candidate records to a separate versioned Gold dataset.

#### Scenario: Promote reviewed candidates
- **WHEN** the project owner confirms that all candidate questions, answers, pages, and evidence were manually checked without error
- **THEN** the system writes a separate review record for every question with reviewer identity, review timestamp, PDF/page/text/answer verification, direct relevance labels, and reviewed answer or no-answer decision
- **AND** the existing Gold publication gate validates and publishes the records without modifying the candidate files.

### Requirement: Comparable real-module ablations
The evaluator MUST run the same Gold records against five explicitly named configurations using the project's existing retrieval, semantic, rerank, and hierarchy modules.

#### Scenario: Run all ablations
- **WHEN** evaluation starts with compatible SQLite, semantic sidecar, and Gold hashes
- **THEN** it runs lexical structured, semantic only, hybrid RRF, hybrid plus rerank, and hybrid plus rerank plus hierarchy configurations
- **AND** every configuration uses the same scope, candidate pool, K values, and Gold relevance definition.

### Requirement: Standard retrieval and refusal metrics
The evaluator MUST calculate auditable positive-retrieval, no-answer, scope-isolation, and latency metrics.

#### Scenario: Aggregate evaluation metrics
- **WHEN** all queries finish for a configuration
- **THEN** the report includes Hit, Recall, and Precision at 1/3/5/10, MRR@10, nDCG@10, no-answer precision/recall/F1, answerable false-abstention rate, scope leakage rate, success/error counts, and P50/P95 latency
- **AND** metric denominators and formulas are documented.

### Requirement: Direct evidence remains distinct from context
The evaluator MUST NOT count parent or expanded context as direct relevant evidence unless it is explicitly present in the Gold direct chunk IDs.

#### Scenario: Evaluate hierarchical output
- **WHEN** hierarchy adds parent or same-heading chunks around an anchor
- **THEN** direct retrieval metrics use only Gold direct chunk IDs
- **AND** context coverage and context overhead are reported separately.

### Requirement: Reproducible result package
The evaluation MUST produce machine-readable per-query results and human-readable comparison and failure reports bound to input hashes and runtime configuration.

#### Scenario: Complete an evaluation run
- **WHEN** all configurations have completed
- **THEN** the output contains per-query JSONL, summary JSON/CSV/Markdown, type breakdown, failure cases, environment/config manifest, and exact rerun commands
- **AND** the manifest records Gold, database, semantic index, model, script, and configuration hashes or identities.
