## 1. Reproduction and Service Fix

- [x] 1.1 Add tests reproducing five-item single-response risk, bounded batches, cumulative preview, and exact identity order.
- [x] 1.2 Add fallback-cause classification tests for incomplete JSON, invalid schema, provider failure, and generic processing errors.
- [x] 1.3 Implement bounded three-item batching, larger per-batch output budgets, and classified audit/summary wording.

## 2. GUI Completion Semantics

- [x] 2.1 Add tests for 100% completion after reviewed no-render and blocked workflows.
- [x] 2.2 Add tests that confirmed review reloads persisted Markdown into current-file preview.
- [x] 2.3 Implement terminal progress/event transitions and reviewed-preview refresh without weakening rendering or release gates.

## 3. Verification

- [x] 3.1 Run syntax checks and focused construction service/review/GUI tests.
- [x] 3.2 Run the full maintained test suite and inspect warnings or regressions.
- [x] 3.3 Validate `fix-construction-stream-truncation-and-completion` strictly and record results.

## Verification (2026-09-20)

- Preserved production artifact confirmed `Invalid JSON: EOF while parsing a string at line 1 column 20394`, proving remote content arrived and was truncated before strict validation.
- `py_compile` passed for the construction service and GUI modules.
- Focused construction service/review/GUI/layout suite passed with `131 passed`.
- Full maintained suite passed with `460 passed, 12 warnings`; warnings are existing Docling and RapidOCR deprecation warnings.
- The preserved EOF signature is now classified as `incomplete_structured_output` with truthful user-facing wording.
- `openspec validate fix-construction-stream-truncation-and-completion --strict` passed.
