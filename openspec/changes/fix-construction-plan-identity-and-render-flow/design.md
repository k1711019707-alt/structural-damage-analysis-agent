## Context

`GenerationContext.prompt` is built from the complete construction evidence. The construction service later creates one request per repair line for plans larger than one item, but currently passes that full prompt unchanged. The request payload also contains a subset report and one deterministic repair line. This creates two competing sources of identity and allows a model or compatible gateway to select an unrelated finding. The local `_validate_exact_identities` guard correctly rejects that result, but the resulting local fallback is intentionally non-renderable.

## Decisions

### Use request-scoped business context

For construction-plan requests, send `generation_context.profile_prompt` as the user-configured business instruction and construct a request-scoped context block from the subset report, deterministic repair lines, allowed source markers, and selected knowledge chunks. Do not send `generation_context.prompt`, because it embeds the complete batch evidence and is not safe for a single-finding request.

### Make the requested identity explicit

Include `requested_work_item_identity` and `requested_work_item_identities` in the payload and repeat the exact `image_name#finding_index` in the construction instructions. The strict response schema remains unchanged; local validation remains authoritative.

### Keep conservative fallback and rendering boundaries

An identity mismatch still raises `ConstructionPlanGenerationError` and uses the existing local fallback. Fallback work items remain blocked by `_construction_render_allowed`, because their narrative content is a guard marker rather than an authored construction plan. A `remote_ai` plan with `review_status=confirmed_by_engineer`, `plan_status` of `pending_engineer_review` or `hold`, and `construction_released=false` can render a preview; `hold` remains visibly non-released.

## Verification

1. Unit-test that the request payload contains only the requested report finding and explicit identity, while the full context prompt is absent.
2. Keep the existing identity mismatch test and add a service-level request fixture that would previously select the first full-batch finding.
3. Test the GUI render predicate for confirmed remote `hold` and confirmed local-fallback plans.
4. Run focused construction/GUI tests, syntax compilation, and the maintained suite with offscreen Qt.
