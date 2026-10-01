## Why

A real five-item construction run streamed remote content successfully but the single response was truncated at character 20,394, failed strict JSON validation with an EOF error, and was then mislabeled as remote-service unavailability. After engineering review, terminal workflows that skipped or were blocked from optional rendering also remained at 75%, and the current-file preview retained pre-review Markdown.

## What Changes

- Proactively split construction generation into smaller batches before the observed response size can exceed compatible-provider output limits.
- Increase the per-batch structured-output budget while preserving strict schema validation, retries, timeout, cumulative streaming, and local fallback.
- Classify construction fallback causes so incomplete or invalid returned drafts are not reported as provider unavailability.
- Complete reviewed workflows at 100% when no rendering remains or engineering status blocks rendering; continue through the existing rendering progress when rendering starts.
- Reload reviewed construction Markdown into the current-file preview so review status, reviewer, and notes are current.

## Capabilities

### New Capabilities

- `truthful-construction-completion`: Reliable streamed construction batching, truthful fallback reporting, reviewed-preview refresh, and terminal progress semantics.

### Modified Capabilities


## Impact

- `runtime/responses_construction_plan.py`: batch sizing, output budget, and fallback-cause classification.
- `runtime/damage_workflow_gui.py`: truthful fallback status, reviewed preview refresh, and 100% terminal progress.
- Construction service and GUI tests: truncation prevention, fallback wording, progress completion, and refreshed preview coverage.
- No change to cancellation, 180-second timeout, retry count, strict validation, active-v2 RAG, review safety gates, or construction release rules.
