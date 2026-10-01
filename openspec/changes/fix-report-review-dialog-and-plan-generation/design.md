## Context

The v3 workflow correctly requires human confirmation, but the first implementation placed the editor inline in the main window. After confirmation, `build_plan()` loaded a `DamageReport` model and embedded that object directly in the evidence mapping. `_retrieve_profile_knowledge()` tolerated it, but `build_generation_context()` serializes the complete evidence mapping and therefore raised a deterministic `TypeError` before construction-plan generation could begin.

## Goals / Non-Goals

**Goals:**

- Make the report-to-plan handoff JSON-safe and regression-tested.
- Present report review as an explicit modal workflow after report generation.
- Support editing, draft saving, confirmation, cancellation, validation feedback, and later reopening.
- Preserve the v3 identity/integrity/reviewer gate and active-v2 RAG metadata.

**Non-Goals:**

- Change report schema v3, damage grading semantics, or construction-plan business content.
- Weaken the human-confirmation gate.
- Add a new external dependency or change provider configuration.

## Decisions

1. Add a dedicated `DamageReportReviewDialog` owned by the main window. A modal dialog makes the required review step explicit, retains normal window controls, and prevents accidental downstream continuation while review is unresolved.
2. The dialog edits the complete v3 JSON representation and supplies reviewer identity separately. Saving persists `edited_pending_confirmation`; confirming reuses `save_human_reviewed_report()` so system-owned provenance and evidence identities remain protected.
3. Closing or cancelling the dialog is non-destructive. It leaves the persisted report pending and exposes a “审核分析报告” button in the report-file row for reopening.
4. Convert the loaded confirmed report with `model_dump(mode="json")` before building construction evidence. Also normalize model/dataclass values at the `GenerationContext` boundary as defense in depth, without accepting arbitrary stringification.
5. Construction generation starts only after the dialog returns confirmed. Failure restores an actionable status and keeps the confirmed report available for retry.
6. The report-row review button is contextual rather than a persistent generation-status display. It is visible for pending review and failed-plan retry, then hidden as soon as confirmation starts construction generation. Construction progress remains in the existing current-file preview and status surfaces.
7. Remove the report-row “读取 Word / PDF” button. Generated report and construction-plan opening remain available through their dedicated actions.
8. Reuse the existing end-to-end `recognition_progress` value as the source for a visible horizontal `QProgressBar` in the former report-file band. Connect it to the retained circular indicator so wide-screen behavior stays synchronized.
9. Use a two-row right action panel. “打开分析报告/打开施工方案” occupy the upper row; “启动/生成修复渲染图” occupy the lower row. The pending-review/retry action remains contextual at the end of the progress band.
10. Give the project-overview title a dedicated compact style and fixed responsive header height. Change the editor from an ignored vertical policy to an expanding policy and raise its responsive height range, preserving the 4000-character limit and raw text behavior.
11. Build the event-log panel with the same top margin, spacing, `panelTitle` style, and fixed title policy as the current-file panel. Remove the log editor's fixed maximum height and give it an expanding vertical policy so the display occupies the remaining panel space.

## Risks / Trade-offs

- [Raw JSON is technical for non-developer reviewers] → Keep formatted indentation, explanatory text, and schema validation messages; no fields are silently discarded.
- [A modal dialog can be closed accidentally] → Closing behaves like cancel, persists no confirmation, and leaves an obvious reopen button.
- [Nested non-JSON values could reappear in future evidence] → Normalize supported Pydantic/dataclass/path/date values before hashing and prompt serialization and fail clearly on unsupported objects.

## Migration Plan

1. Add failing tests reproducing the `DamageReport` serialization error and asserting modal review behavior.
2. Add JSON normalization and fix the construction evidence mapping.
3. Add the review dialog and replace the inline editor workflow.
4. Run focused GUI/downstream tests, the full maintained suite, an offscreen dialog visual smoke test, and strict OpenSpec validation.

Rollback consists of reverting this change; persisted `damage-report.v3` files remain compatible.

## Open Questions

None for this repair.
