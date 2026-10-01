## 1. Reproduction and serialization boundary

- [x] 1.1 Add a regression test reproducing confirmed `DamageReport` serialization during construction evidence assembly.
- [x] 1.2 Normalize confirmed reports and supported evidence values to deterministic JSON-compatible mappings before retrieval/context construction.

## 2. Modal human review workflow

- [x] 2.1 Add a modal damage-report review dialog with editable formatted JSON, reviewer input, status/help text, save-draft, confirm-and-continue, and cancel controls.
- [x] 2.2 Open the dialog after automatic and manual report completion, keep cancellation pending, and add a reopen-review action.
- [x] 2.3 Reuse v3 save/confirmation validation and ensure downstream generation triggers exactly once after confirmation.
- [x] 2.4 Remove the embedded always-visible editor and preserve a compact main-workbench status surface.

## 3. Plan handoff and verification

- [x] 3.1 Restore construction generation completion/failure status and retry behavior around the confirmed report.
- [x] 3.2 Add modal GUI contract, cancellation, editing, confirmation, JSON handoff, and offscreen visual tests.
- [x] 3.3 Run affected syntax checks, focused tests, the full maintained suite, offscreen GUI smoke, and strict OpenSpec validation.

## Verification (2026-09-20)

- `py_compile` passed for `runtime/generation_context.py` and `runtime/damage_workflow_gui.py`.
- Focused report/review/plan/GUI/responsive suite: `97 passed` after the report-row and action-panel layout changes.
- Real local-fallback handoff test persisted `repair_plan.json`, `construction_plan.json`, and `construction_plan.md` from a confirmed v3 report.
- Offscreen review dialog visual smoke passed at `1100x760` and `900x650` with no control overlap.
- Offscreen main-workbench inspection passed at `1680x980` and `1440x980`; document actions are above primary actions and the progress band is unobstructed.
- Project-overview visual inspection passed at `1440x980`, `1680x980`, and `2048x1230`; the compact heading, enlarged editor, and progress band remain separated.
- Event-log visual inspection passed at `1440x980`, `1680x980`, and `2048x1230`; its heading top matches the current-file heading and the expanded display remains within the log panel.
- Full maintained suite: `438 passed, 2 failed, 12 warnings`; both failures are pre-existing read-only assistant isolation failures in `tests/test_readonly_assistant.py` caused by global history results overriding the injected response stream.
- `openspec validate fix-report-review-dialog-and-plan-generation --strict` passed.

## 4. Contextual report-row cleanup

- [x] 4.1 Remove the “读取 Word / PDF” button and its window-only dispatch method.
- [x] 4.2 Keep the review/retry action visible only when human action is required; hide it while construction generation is active and after success.
- [x] 4.3 Update GUI contracts and offscreen visual coverage, then rerun focused tests and strict OpenSpec validation.

## 5. Progress band and action hierarchy

- [x] 5.1 Replace the report-file band with a synchronized horizontal workflow progress bar.
- [x] 5.2 Move generated-document actions into an upper action row and primary start/render controls into a lower row.
- [x] 5.3 Add responsive layout assertions and inspect offscreen screenshots at supported desktop sizes.

## 6. Project-overview editor sizing

- [x] 6.1 Add a dedicated compact project-overview heading style and responsive header height.
- [x] 6.2 Increase the multiline editor's responsive minimum/maximum height and use an expanding vertical policy.
- [x] 6.3 Verify title/editor/progress separation across supported viewports and inspect offscreen screenshots.

## 7. Event-log alignment and display size

- [x] 7.1 Give the event-log heading the same style, fixed policy, margins, and spacing as the current-file heading.
- [x] 7.2 Remove the event-log height cap and make its display expand to fill available panel space.
- [x] 7.3 Verify heading alignment and log-display containment across supported viewports and inspect offscreen screenshots.
