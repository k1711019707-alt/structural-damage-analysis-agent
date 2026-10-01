## 1. Structured Review Form

- [x] 1.1 Replace the editable JSON surface with tabbed overall, findings, review/limitations, and read-only advanced-data views.
- [x] 1.2 Add Chinese damage-level mapping, wrapped text controls, blank optional values, and readable protected standards rows.
- [x] 1.3 Reconstruct `DamageReport` from allowlisted form fields while preserving schema, provenance, integrity, finding order, and identities.

## 2. Persistence and Interaction

- [x] 2.1 Connect draft save and confirmation to the existing persistence service and refresh controls from the persisted report.
- [x] 2.2 Synchronize the read-only advanced JSON view from current form state and keep validation feedback actionable.

## 3. Verification

- [x] 3.1 Update focused dialog tests for localized controls, null handling, reconstruction, immutable fields, read-only JSON, and existing gates.
- [x] 3.2 Update and inspect offscreen visual smoke at 1100x760 and 900x650 for reachability, wrapping, and overlap.
- [x] 3.3 Run syntax checks, focused maintained tests, and strict OpenSpec validation; record verification results.

## Verification (2026-09-20)

- `py_compile` passed for the dialog implementation and its focused test modules.
- Focused report/schema/persistence/construction handoff/responsive suite passed with `92 passed` before concurrently updated construction-worker tests entered the workspace.
- Exact structured-review behavior tests passed with `5 passed` after the concurrent update.
- Offscreen GUI visual smoke passed with `2 passed`; screenshots were inspected at 1100x760 and 900x650 for the overall, findings, and reviewer views without overlap or clipped actions.
- After the concurrent construction-worker implementation landed, the maintained full suite reached `438 passed, 2 failed, 12 warnings`. Both remaining failures are outside this review-dialog change: the new construction worker calls `AppSettings.model_copy()`, but the current settings model does not expose that interface.
- `openspec validate human-friendly-report-review --strict` passed.
