## ADDED Requirements

### Requirement: Comparable adaptive retrieval ablations
The evaluator MUST compare legacy and adaptive retrieval configurations on the same versioned Gold, scope, SQLite, semantic sidecar, candidate budget, and direct relevance definition.

#### Scenario: Run evaluation v2
- **WHEN** the v2 evaluation starts
- **THEN** it evaluates legacy lexical, semantic, and hybrid baselines plus adaptive hybrid, adaptive rerank, and gated hierarchy configurations without modifying the inputs or evaluation_v1.

### Requirement: External fallback routing metrics
The evaluator MUST treat Gold records with no knowledge-base answer as expected external fallback cases and MUST report route and labeling correctness instead of requiring refusal.

#### Scenario: Score a knowledge-base no-answer query
- **WHEN** a Gold record has `expected_no_answer=true`
- **THEN** the evaluator expects an external fallback route and scores fallback routing accuracy, web-search preference, model-prior fallback, and source-mode labeling.

### Requirement: Auditable quality and latency report
The evaluator MUST report direct retrieval, scope leakage, route, hierarchy coverage/overhead, and latency metrics with raw per-query evidence and input hashes.

#### Scenario: Complete the v2 run
- **WHEN** all configured query runs finish
- **THEN** evaluation_v2 contains per-query JSONL, summaries, type breakdowns, failures, manifest, formulas, hashes, and exact rerun commands.

### Requirement: Recall regression gate
The evaluator MUST prominently compare adaptive hybrid deep recall against the legacy lexical baseline and MUST report a failed gate when adaptive Hit@30 remains below the lexical Hit@30 target.

#### Scenario: Evaluate candidate-pool repair
- **WHEN** adaptive and lexical results are aggregated
- **THEN** the report states whether adaptive Hit@30 is at least the measured lexical Hit@30 and does not hide a regression behind higher rank metrics.
