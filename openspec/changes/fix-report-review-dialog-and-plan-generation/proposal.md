## Why

The confirmed damage report currently enters construction-plan retrieval as a Pydantic `DamageReport` object, while `GenerationContext` requires JSON-serializable evidence, so the workflow fails with `TypeError: Object of type DamageReport is not JSON serializable`. The report review experience is also embedded in the crowded workbench instead of opening an explicit edit-and-confirm dialog when report generation finishes.

## What Changes

- Normalize the confirmed report to a JSON-compatible dictionary before it crosses the retrieval and generation-context boundary.
- Open a modal report-review dialog immediately after report generation completes.
- Let the reviewer edit the full structured v3 report, enter a reviewer name, save a pending draft, or confirm and continue.
- Keep the report pending and block downstream planning when the dialog is cancelled or validation fails.
- Continue construction-plan generation only after successful confirmation, with clear status restoration when generation succeeds or fails.
- Remove the always-visible embedded review editor from the main workbench while keeping a reopen-review action for pending reports.
- Show the compact review/retry action only while user action is required; hide it during and after successful construction-plan generation instead of displaying “施工方案生成中/已生成”.
- Remove the “读取 Word / PDF” action from the report-file row.
- Replace the former report-file row with a full-width linear workflow progress band.
- Recompose the right-side action panel into two rows: generated-document actions above, primary start/render controls below.
- Compact the project-overview heading and allocate more vertical space to its multiline input without changing its data contract.
- Align the event-log heading with the current-file heading and let the log display expand to use the available panel height.

## Capabilities

### New Capabilities

- `report-review-dialog-and-plan-handoff`: Modal review, editable confirmation, JSON-safe plan handoff, and downstream status behavior.

### Modified Capabilities


## Impact

- `runtime/damage_workflow_gui.py`: report-completion flow, modal dialog UI, review actions, and construction evidence assembly.
- `runtime/generation_context.py`: optional defensive JSON-compatible normalization at the evidence boundary.
- GUI and integration tests for the reported failure and modal review behavior.
