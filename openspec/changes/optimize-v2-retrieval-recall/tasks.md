## 1. Query analysis

- [ ] 1.1 Add bounded Chinese query normalization, keyword extraction, character n-gram fallback, and standard/clause identifier parsing.
- [ ] 1.2 Add unit coverage for continuous Chinese, mixed-language, standard-number, and clause-number queries.

## 2. Multi-channel retrieval

- [ ] 2.1 Implement scoped FTS5/BM25, LIKE, and exact metadata candidate channels for v2.
- [ ] 2.2 Merge candidates by chunk ID with weighted RRF/fusion scores and retrieval diagnostics.
- [ ] 2.3 Preserve child-only ranking, parent hydration, max_chars, quality metadata, and scoped fallback semantics.
- [ ] 2.4 Keep legacy knowledge database retrieval compatible with improved query term generation.

## 3. Validation

- [ ] 3.1 Run focused retrieval tests and natural Chinese regression queries against representative v2 payloads.
- [ ] 3.2 Run the complete project test suite and validate the OpenSpec change.
