## 1. OpenSpec

- [x] 1.1 Create proposal, design, capability delta, and verification tasks.

## 2. Implementation

- [x] 2.1 Add authoritative reviewed-plan render selection.
- [x] 2.2 Wire the GUI to enqueue every reviewed work item when rendering is enabled, including confirmed local-fallback and evidence-inconsistent previews.
- [x] 2.3 Add regressions for missing/incomplete detection metadata and preserve review/release gates.

## 3. Verification

- [x] 3.1 Run focused render-selection and GUI tests: 76 passed.
- [x] 3.2 Run syntax checks and the maintained full suite: `py_compile` passed; 532 passed, 12 existing warnings.
- [x] 3.3 Validate this OpenSpec change strictly and record results.

## Verification record (2026-09-21)

- Confirmed remote-authored, local-fallback, and evidence-inconsistent reviewed plans are renderable previews while unreleased.
- Reviewed plan items are enqueued even when detection metadata is missing, failed, or has no boxes.
- Missing source/provider errors remain renderer failures rather than silent skipped items.
- `openspec validate enforce-enabled-repair-rendering --strict` passed.
