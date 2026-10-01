## 1. Adaptive retrieval

- [x] 1.1 Add query type classification and route diagnostics with backward-compatible defaults.
- [x] 1.2 Implement lexical/semantic/exact reserved candidate union and adaptive fusion weights.
- [x] 1.3 Reduce source-title influence inside explicit scope while preserving document discovery.
- [x] 1.4 Add off/auto/forced hierarchy gating and bounded context expansion.

## 2. External answer fallback

- [x] 2.1 Add answer-source mode and external fallback metadata to generation/runtime context contracts.
- [x] 2.2 Implement verified web-search capability routing and safe model-prior fallback without exposing credentials.
- [x] 2.3 Update generation instructions and provenance so external answers cannot masquerade as knowledge-base evidence.

## 3. Evaluation v2

- [x] 3.1 Extend the evaluator with legacy/adaptive configurations and external fallback routing metrics.
- [x] 3.2 Add tests for route classification, candidate reserves, title isolation, hierarchy gates, external fallback, and metric formulas.
- [x] 3.3 Run the complete 150-question evaluation into a new evaluation_v2 directory and inspect recall, routing, leakage, hierarchy, and latency results.

## 4. Validation

- [x] 4.1 Run focused and full regression tests using the project environment.
- [x] 4.2 Recalculate reports from raw outputs, validate input hashes, and run strict OpenSpec validation.
