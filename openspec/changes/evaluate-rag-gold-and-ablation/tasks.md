## 1. Gold promotion

- [x] 1.1 Convert the user-confirmed 150 candidate records into a separate attested review file.
- [x] 1.2 Run the existing Gold publication gate and verify Gold count, labels, evidence, and hashes.

## 2. Evaluation runner

- [x] 2.1 Implement the five existing-module ablation configurations with fixed scope and candidate-pool controls.
- [x] 2.2 Implement Hit/Recall/Precision@K, MRR, nDCG, refusal, leakage, latency, and hierarchy-context metrics.
- [x] 2.3 Write resumable per-query outputs, configuration manifest, summaries, type breakdowns, and failure reports.

## 3. Tests and execution

- [x] 3.1 Add unit tests for metric formulas, direct-vs-context separation, no-answer classification, and scope leakage.
- [x] 3.2 Run the complete 150-question five-configuration experiment using the current SQLite and semantic sidecar.
- [x] 3.3 Recalculate summaries from raw results and inspect representative wins/failures by query type.

## 4. Validation and reporting

- [x] 4.1 Run focused RAG regression tests and strict OpenSpec validation.
- [x] 4.2 Deliver a Chinese evaluation report with measured conclusions, limitations, and recommended next changes.
