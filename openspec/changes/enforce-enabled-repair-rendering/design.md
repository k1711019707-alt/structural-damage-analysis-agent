## Context

`DamageRepairPlanner.build_targeted_render_selection()` currently iterates image results first and only accepts a result when it is successful, has detection findings, has an exact `image_name` match, and contains a non-empty method/name pair. This is appropriate for an optional detection-driven preview, but it violates the user-facing contract once an engineer has confirmed a remote construction plan and enabled repair rendering.

## Decisions

### Plan items are authoritative

Add a plan-driven selector that iterates confirmed work items in plan order. Each item carries `original_image_path`, `image_name`, and the reviewed repair method. The selector uses the item path directly; if absent, it resolves a matching path from image results by image name. It creates an item whenever the reviewed method is present, even if detection status is failed or no detection boxes are available.

### No silent skip after enablement

Missing source files remain queued so the renderer returns an auditable failed result. Provider failures likewise remain failed results. The GUI may report diagnostics, but it must not mark the render stage complete with zero items merely because detection metadata was incomplete.

### Preserve review and release gates

The queue is only built after engineer confirmation and a non-released pending/hold/evidence-inconsistent plan status. A local fallback or evidence-inconsistent plan may be rendered only after engineer confirmation, and the UI must label it as a conservative pending-review preview that is not construction authorization. This change does not authorize rendering an unreviewed or released plan.

## Verification

1. A reviewed plan item with no matching detection result still produces one render queue item.
2. A reviewed plan item with a differing image-name representation resolves its original path from the plan or result path.
3. GUI dispatch sends every reviewed plan item to the renderer and does not show the skipped-stage message.
4. Run focused tests, full suite, syntax checks, and strict OpenSpec validation.
