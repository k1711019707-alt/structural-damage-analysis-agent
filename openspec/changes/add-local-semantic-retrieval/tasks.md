## 1. Contracts and configuration

- [ ] 1.1 Define semantic sidecar manifest and candidate result contracts without changing v2 SQLite schema.
- [ ] 1.2 Add configurable model path, model name, batch size, candidate_k, and semantic weight defaults.

## 2. Embedding index

- [ ] 2.1 Implement `knowledge_pipeline/embed.py` for retrieval-child vectorization, normalization, fingerprints, and atomic sidecar writes.
- [ ] 2.2 Implement incremental/stale-entry validation and CLI diagnostics.

## 3. Semantic retrieval

- [ ] 3.1 Implement `knowledge_pipeline/semantic_retrieve.py` with optional sentence-transformers adapter and NumPy cosine search.
- [ ] 3.2 Enforce document scope, child-only filtering, content-hash checks, and structured unavailable/fallback statuses.

## 4. Hybrid integration

- [ ] 4.1 Extend `retrieve.py` with an optional semantic retriever callback and semantic RRF fusion.
- [ ] 4.2 Preserve lexical-only and legacy behavior when semantic dependencies are absent.
- [ ] 4.3 Carry semantic scores, ranks, channels, and fallback diagnostics into result metadata.

## 5. Validation

- [ ] 5.1 Add unit tests with a deterministic fake embedder for index creation, scoped search, stale entries, and hybrid fusion.
- [ ] 5.2 Run focused and full project tests plus OpenSpec strict validation.
- [ ] 5.3 Benchmark semantic versus lexical recall on representative Chinese engineering queries before enabling it by default.
