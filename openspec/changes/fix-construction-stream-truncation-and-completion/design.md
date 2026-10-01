## Context

The preserved production artifact proves the transport completed and emitted streamed JSON, but `ConstructionPlanDraft.model_validate_json()` received an unterminated string at character 20,394. Five work items were sent in one request because chunking started only above eight items, and the requested output budget was only 11,000 tokens. The worker catches every generation exception in one branch, so this post-transport validation failure produced the same “remote service unavailable” local draft used for connection failures.

Review persistence correctly updated JSON and Markdown to `confirmed_by_engineer`, but the GUI did not reload the Markdown after review and did not define a terminal progress transition when optional rendering was absent or prohibited.

## Goals / Non-Goals

**Goals:**

- Prevent the observed multi-item construction response from being truncated under the application-requested output limit.
- Preserve cumulative streaming and exact work-item identity/order across batches.
- Report whether fallback followed transport failure, incomplete JSON, schema rejection, or another local processing failure.
- Make 100% mean all applicable workflow work has ended, including a reviewed plan that legitimately cannot render.
- Keep current-file content synchronized with the persisted reviewed plan.

**Non-Goals:**

- Do not accept partial JSON, relax the schema, or assemble unvalidated remote text.
- Do not add cancellation, shorten timeout, change retry count, or add bounded fallback waiting.
- Do not turn review into construction release or unblock `hold`/`evidence_inconsistent` plans.
- Do not automatically rerun the already persisted local fallback artifact.

## Decisions

### Batch more than three work items

Use batches of at most three work items and route any plan above three items through the existing merge path. The observed five-item response reached 20,394 characters, while splitting it produces two independently validated responses with substantially smaller JSON documents. The existing identity and merge validation remains authoritative.

Only increasing the token limit was rejected as the sole fix because compatible gateways may impose their own caps. Only reactive retry was rejected because it would make users wait through a known oversized response before taking the safer path.

### Raise the structured-output budget per bounded batch

Request a 24,000-token budget for a three-item batch, bounded by the existing 32,000 application ceiling. This provides headroom for verbose Chinese lists without allowing unlimited output. Smaller batches receive proportionate but no less than 16,000 tokens.

### Classify fallback without weakening fallback behavior

Derive a credential-safe category from the exception text: `incomplete_structured_output`, `invalid_structured_output`, `provider_unavailable`, or `generation_processing_error`. Store the category in generation audit and use category-specific local-draft and GUI wording. Preserve the redacted detailed reason for diagnosis.

### Terminal progress follows remaining work

After confirmation, start rendering only when selected and allowed. Otherwise set progress to 100 and log a workflow-complete milestone, including an explicit blocked-completion message for `hold` and `evidence_inconsistent`. A blocked engineering disposition is a completed software workflow, not an incomplete task.

### Reload reviewed Markdown

After review persistence, read `construction_plan.md` and replace the current-file content before setting its final review status. This mirrors the persisted artifact and avoids showing pending review metadata after confirmation.

## Risks / Trade-offs

- [More provider calls for plans with four or more findings] -> Bound each batch to three and retain cumulative preview so progress remains visible.
- [Different batches repeat global fields] -> Keep the established deterministic merge, first-draft scope/summary rule, and unique-list aggregation.
- [Fallback classification depends on exception shape] -> Match stable JSON/Pydantic EOF and validation phrases, retain a general processing category for unknown cases, and test representative errors.
- [Progress reaches 100 for a blocked plan] -> Keep stage/status text explicitly blocked so completion cannot be mistaken for engineering approval or construction release.

## Migration Plan

1. Add failing tests from the preserved 20,394-character EOF signature and reviewed GUI state.
2. Implement batching/budget and fallback classification.
3. Implement reviewed-preview and terminal-progress transitions.
4. Run focused and full tests plus strict OpenSpec validation.

No persisted schema migration is required. Existing fallback artifacts remain auditable and can be regenerated explicitly through the normal workflow.

## Open Questions

None.
