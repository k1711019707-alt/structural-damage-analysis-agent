## 1. Report content

- [x] 1.1 Create the OpenSpec proposal, design, capability deltas, and verification tasks.
- [x] 1.2 Add deterministic multi-finding summary and overall-level rationale assembly.
- [x] 1.3 Preserve single-finding provider-authored summary behavior.

## 2. Render prompt audit

- [x] 2.1 Verify the configured prompt and reviewed method reach the FHL image-edit request.
- [x] 2.2 Verify the configured prompt and reviewed method reach the SiliconFlow image-edit request.
- [x] 2.3 Add or update tests for evidence-first constraints and preview-only semantics.

## 3. Verification

- [x] 3.1 Run focused report and renderer tests.
- [x] 3.2 Run syntax checks and the maintained full suite.
- [x] 3.3 Validate this OpenSpec change strictly and record results.

## Verification record

- Focused tests: 76 passed.
- Syntax compilation: `runtime/responses_damage_report.py`, `runtime/fhl_repair_renderer.py`, `runtime/settings_models.py`, and changed tests compiled successfully.
- Full suite: 529 passed, 12 existing dependency deprecation warnings.
- OpenSpec strict validation: passed.
