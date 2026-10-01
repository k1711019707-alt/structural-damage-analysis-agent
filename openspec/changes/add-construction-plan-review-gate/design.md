## Context

The damage-report stage already pauses at a structured modal review and persists an explicit human confirmation before starting construction generation. The construction-plan stage currently persists a safety-limited draft, marks the stage complete, and may immediately start repair rendering. Its ordinary worker progress handler also appends every provider attempt, route, streaming completion, and local assembly message to the permanent event log.

The construction plan contains two different kinds of state that must not be conflated: `plan_status` is a deterministic engineering disposition (`pending_engineer_review`, `hold`, or `evidence_inconsistent`), while human review records who checked the editable narrative. Review must never convert a blocked disposition into permission to render or set construction release.

## Goals / Non-Goals

**Goals:**

- Provide a structured construction-plan review experience consistent with the damage-report dialog.
- Allow engineers to edit descriptive plan content while protecting evidence identities, method selection, measured values, safety stops, provenance, and audit data.
- Persist draft and confirmed reviews atomically in the existing construction envelope.
- Prevent repair rendering before successful review and continue blocking rendering for `hold` or `evidence_inconsistent` plans.
- Keep the event log concise and stage-oriented while preserving detailed generation feedback elsewhere.

**Non-Goals:**

- Do not grant construction release or introduce an in-application release workflow.
- Do not allow reviewers to change deterministic method selection, report facts, evidence counts, identities, scale fields, stop-work conditions, exclusions, or provenance.
- Do not alter provider routing, timeout, retry, streaming, local fallback, active-v2 RAG, or cumulative preview behavior.
- Do not add construction-generation cancellation or bounded fallback waiting.

## Decisions

### Separate review state from engineering disposition

Add `review_status` and a `ConstructionPlanReview` object with backward-compatible defaults. Confirmation sets both review fields to `confirmed_by_engineer`, but leaves `plan_status` and `construction_released` unchanged. This preserves the meaning of `hold` and `evidence_inconsistent` and keeps old construction artifacts loadable.

Changing `plan_status` to a reviewed value was rejected because it would erase the safety disposition and weaken downstream blocking checks.

### Structured modal plus persistent re-entry action

Add `ConstructionPlanReviewDialog` using the same tabbed operational pattern as the report review: overall, work items, general requirements, review, and read-only advanced data. Closing the dialog leaves the plan pending and exposes an `审核施工方案` button so review is not lost behind a modal lifecycle.

The editable allowlist covers scope, summaries, site checks, materials, equipment, procedure, quality, acceptance, safety controls, assumptions, and general requirements. Reconstruction starts from the last validated model and restores all protected fields before persistence.

### Atomic envelope-preserving persistence

Add construction-plan load and save helpers. The save helper reads the existing JSON envelope, replaces only `construction_plan`, atomically rewrites JSON and Markdown, and preserves the repair-plan snapshot, fallback fields, generation audit, manifest, and provenance. It validates immutable fields against the persisted original before writing.

### Rendering requires two independent conditions

Rendering is available only when `review_status == confirmed_by_engineer` and `plan_status == pending_engineer_review`. A confirmed `hold` or `evidence_inconsistent` plan remains blocked. `construction_released` remains false in every path because rendering is a visualization step, not construction authorization.

### Permanent log records milestones only

Worker progress continues to update the progress bar, status label, generation-status label, and current-file preview. The progress handler no longer appends ordinary status text to the event log. Explicit lifecycle handlers log stage start, stage completion, review wait, review confirmation or pending close, concise fallback use, and terminal failure.

## Risks / Trade-offs

- [Large work-item plans make the dialog long] -> Use tab-local scroll areas and one grouped editor per work item.
- [A future schema field is accidentally made editable] -> Reconstruct from a deep copy and replace only an explicit allowlist; compare protected fields during persistence.
- [Old artifacts lack review metadata] -> Use model defaults so loading old `construction-plan.v2` envelopes yields a pending review.
- [Review confirmation is mistaken for formal construction approval] -> Keep a persistent warning banner, explicit confirmation button text, and `construction_released=false` in model, audit, and Markdown.
- [Rendering is triggered by stale checkbox state] -> Schedule rendering only from the confirmed-review handler after both review and disposition checks pass.

## Migration Plan

1. Add schema defaults and persistence helpers with compatibility and immutability tests.
2. Add the structured dialog and focused offscreen Qt tests.
3. Insert the post-generation review gate, visible re-entry action, and rendering checks.
4. Filter construction worker progress from the event log while asserting detailed status remains visible.
5. Run focused and full test suites plus strict OpenSpec validation.

Rollback consists of reverting this change. Existing envelopes remain readable because review fields are additive and defaulted; artifacts written by this change remain valid `construction-plan.v2` documents.

## Open Questions

None.
