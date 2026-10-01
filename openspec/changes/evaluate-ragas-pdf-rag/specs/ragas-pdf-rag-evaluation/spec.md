## ADDED Requirements

### Requirement: Candidate-scoped end-to-end evaluation

The evaluator MUST use the candidate question/reference/context records and MUST restrict retrieval to each record's candidate document IDs.

#### Scenario: Matching test index

- **WHEN** the candidate set and matching SQLite index are provided
- **THEN** each row receives retrieved contexts, a generated response, and RAGAS metric results or an explicit row-level error.

### Requirement: Auditable output boundary

The evaluator MUST write versioned raw rows, aggregate metrics, manifest, and failures separately from Gold and production artifacts; it MUST retain `gold_label=false` semantics for the input candidate set.

#### Scenario: Evaluation completes

- **WHEN** all rows finish or fail
- **THEN** the manifest reports counts, hashes, model category, metric names, and the output does not modify the input candidate file, Gold Set, SQLite, or semantic index.
