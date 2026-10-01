## Why

After a damage report is confirmed, construction-plan retrieval and remote structured generation run synchronously on the Qt GUI thread. A successful production request took about four minutes and caused Windows to mark the application as not responding, while the visible workflow state remained at 50% and still referred to report review.

## What Changes

- Run the complete post-review construction-plan stage on a dedicated Qt worker thread.
- Deliver stage, progress, cumulative streaming text, success, and failure updates to the GUI through Qt signals.
- Show that construction-plan preparation and remote generation are active instead of leaving the workflow at the report-review state.
- Preserve the current 180-second timeout, two application retries, remote structured draft contract, local fallback, active-v2 RAG metadata, persistence, and final plan handling.
- Do not add construction-plan cancellation or a new bounded-wait/fallback policy.

## Capabilities

### New Capabilities

- `responsive-construction-plan-generation`: Non-blocking post-review construction-plan execution and truthful GUI progress/state reporting.

### Modified Capabilities


## Impact

- `runtime/damage_workflow_gui.py`: construction-plan worker, signal wiring, lifecycle guards, and UI state handlers.
- `tests/test_report_review_dialog_handoff.py`: asynchronous handoff and completion/failure coverage.
- `tests/test_damage_workflow_gui_contract.py`: worker-thread and responsive-state contracts.
- No construction-plan schema, provider, retry, timeout, fallback, knowledge-retrieval, or artifact format changes.
