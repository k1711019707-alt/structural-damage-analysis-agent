## Why

Construction-plan generation currently ends at a persisted draft and can proceed directly to repair rendering without a dedicated engineering-review interaction. Its event log also exposes transport and assembly details that the damage-report stage keeps in transient status surfaces, producing an inconsistent and difficult-to-scan workflow.

## What Changes

- Add a structured, human-readable construction-plan review dialog with overall, work-item, general-requirements, review, and read-only advanced-data views.
- Add backward-compatible construction review metadata and persist draft/confirmed review updates atomically without changing generation audit, deterministic repair facts, provenance, or fallback metadata.
- Require an identified engineer to confirm the construction plan before repair rendering can be enabled or started.
- Keep `construction_released=false` after review and preserve blocking `hold` and `evidence_inconsistent` dispositions.
- Add a visible `审核施工方案` action whenever a generated plan remains pending review.
- Align construction event logging with the damage-report stage: record only stage start/completion, review wait/confirmation, fallback summary, and terminal failure while retaining detailed transport progress in status and current-file preview surfaces.

## Capabilities

### New Capabilities

- `construction-plan-review-gate`: Structured construction-plan review, protected engineering facts, persisted review metadata, rendering gate, and stage-level event-log behavior.

### Modified Capabilities


## Impact

- `runtime/construction_plan_schema.py`: backward-compatible review metadata.
- `runtime/responses_construction_plan.py`: load and atomic human-review persistence helpers plus reviewed Markdown output.
- `runtime/damage_workflow_gui.py`: construction review dialog, visible review action, rendering gate, and event-log filtering.
- Construction-plan and GUI tests: review persistence, immutability, safety blocking, rendering gating, and event-log/status separation.
- Existing provider, retry/timeout, local fallback, active-v2 RAG, and report-review contracts remain unchanged.
