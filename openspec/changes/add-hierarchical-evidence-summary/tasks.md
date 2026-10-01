## 1. Hierarchical retrieval

- [ ] 1.1 Add configurable expansion mode, context budget, heading-path grouping, and anchor/expanded metadata.
- [ ] 1.2 Implement subsection-first expansion with chapter/page fallback while preserving scope and child-only ranking.
- [ ] 1.3 Add deterministic source ordering and anchor-first budget truncation.

## 2. Evidence-grounded generation

- [ ] 2.1 Add damage-grounded-summary generation mode and structured evidence prompt constraints.
- [ ] 2.2 Preserve citations, quality flags, fallback status, and pending engineer review markers.

## 3. Validation and compatibility

- [ ] 3.1 Add tests for subsection grouping, chapter fallback, expansion diagnostics, and max-context budgets.
- [ ] 3.2 Add tests for damage-summary prompt boundaries and insufficient-evidence behavior.
- [ ] 3.3 Run focused/full tests and strict OpenSpec validation.
