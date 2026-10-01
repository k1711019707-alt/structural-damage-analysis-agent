## 1. Reproduction and Contract Tests

- [x] 1.1 Add failing shared-catalog tests for authoritative metadata, duplicate markers, Windows/POSIX basename sanitization, and unresolved markers.
- [x] 1.2 Add failing report Markdown and human-review re-save tests for readable filenames, locations, and preserved internal markers.
- [x] 1.3 Add failing construction Markdown and human-review re-save tests for the same citation contract.

## 2. Shared Citation Implementation

- [x] 2.1 Implement deterministic citation-catalog construction from generation-context retrieval metadata with active-v2 index fallback.
- [x] 2.2 Implement shared exact-marker rendering that preserves audit IDs and labels unresolved sources.

## 3. Report and Construction Integration

- [x] 3.1 Persist and reuse the citation catalog in report generation and review-save envelopes.
- [x] 3.2 Persist and reuse the citation catalog in construction generation and review-save envelopes.
- [x] 3.3 Apply shared citation rendering to all relevant Markdown prose and list fields without modifying strict business schemas.

## 4. Verification

- [x] 4.1 Run syntax checks and focused report/construction citation tests.
- [x] 4.2 Run the full maintained test suite and inspect warnings or regressions.
- [x] 4.3 Validate `display-knowledge-source-filenames` strictly and record results.

## Verification (2026-09-20)

- Confirmed active-v2 document `00474f7b00ed539d4702` resolves to `GB 50010-2010 混凝土结构设计规范-上.pdf`; page markers 31 and 42 exist in the active index.
- `py_compile` passed for the shared citation module and both generation services.
- New citation contract tests first failed on the missing module/catalog behavior and then passed after implementation.
- Focused report, construction, review, and citation suite passed with `105 passed`.
- Live active-v2 fallback resolved the source filename without chunk-level `source_name` metadata.
- Full maintained suite passed with `465 passed, 12 warnings`; warnings are existing Docling and RapidOCR deprecation warnings.
- `openspec validate display-knowledge-source-filenames --strict` passed before implementation and after artifact completion.
