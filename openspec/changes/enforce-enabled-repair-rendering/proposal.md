## Why

After a construction plan is confirmed, the GUI still derives the render queue from successful detection results and exact image-name matches. A plan item can therefore be counted as `skipped_without_plan` or `skipped_no_detection` even though the user enabled repair rendering and the reviewed plan contains a valid source image and method.

## What Changes

- Make confirmed, remote-authored construction-plan work items the source of truth for the repair-render queue when rendering is enabled.
- Use detection results only to resolve a missing original-image path and retain diagnostics; do not silently discard a reviewed work item because detection metadata is incomplete or image names differ.
- Ensure every queued work item reaches the renderer, where missing files or provider failures are reported as failed render results rather than skipped items.
- Preserve existing human-review, evidence-consistency, local-fallback, and non-release gates.

## Capabilities

### Modified Capabilities

- `construction-plan-review-rendering`: enabling repair rendering after a valid reviewed plan must create a render attempt for every reviewed work item.

## Impact

- `runtime/damage_repair_plan.py`: add an authoritative plan-driven render selection path.
- `runtime/damage_workflow_gui.py`: pass reviewed plan source paths and use the authoritative queue.
- Focused GUI and planner tests.
