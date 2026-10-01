## 1. Schema and Persistence

- [x] 1.1 Add backward-compatible construction review metadata without changing plan disposition or release semantics.
- [x] 1.2 Add envelope-preserving load and atomic reviewed-plan save helpers with protected-field validation.
- [x] 1.3 Add focused tests for draft save, reviewer validation, confirmation metadata, release false, and blocked dispositions.

## 2. Structured Review Interface

- [x] 2.1 Add overall, work-item, general-requirement, review, and read-only advanced-data tabs.
- [x] 2.2 Reconstruct plans from an explicit editable allowlist while restoring protected engineering and audit fields.
- [x] 2.3 Add dialog tests for field reachability, read-only data, protected fields, draft persistence, confirmation, and close behavior.

## 3. Workflow and Log Alignment

- [x] 3.1 Add the visible construction-review action and schedule the dialog after successful generation.
- [x] 3.2 Gate manual and automatic repair rendering on confirmed, non-blocked construction review.
- [x] 3.3 Restrict the construction event log to milestones while retaining detailed worker progress in status/current-file surfaces.
- [x] 3.4 Add workflow tests for scheduling, rendering gates, re-entry action, milestone logs, and detailed-status separation.

## 4. Verification

- [x] 4.1 Run syntax checks and focused construction/report/GUI contract tests.
- [x] 4.2 Run the full maintained test suite and inspect any warnings or failures.
- [x] 4.3 Validate `add-construction-plan-review-gate` with strict OpenSpec validation and record results.

## Verification (2026-09-20)

- `py_compile` passed for the construction schema, persistence service, GUI, and focused review tests.
- Focused construction/report/GUI/layout suite passed with `123 passed`.
- Full maintained suite passed with `452 passed, 12 warnings`; warnings are existing Docling and RapidOCR deprecation warnings.
- Offscreen screenshots were inspected at 1120x780 and 900x650 for overall, work-item, and confirmation views without overlap or clipped actions.
- `openspec validate add-construction-plan-review-gate --strict` passed.
