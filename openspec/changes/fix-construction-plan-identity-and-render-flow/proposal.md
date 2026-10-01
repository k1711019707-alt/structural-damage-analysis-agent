## Why

The construction-plan service sends a single-finding request while also appending the full `GenerationContext.prompt`, which contains the complete batch evidence. A provider can therefore author the wrong work-item identity; strict validation rejects the draft and replaces every narrative field with the local-fallback guard text. Because fallback work items are intentionally not renderable, an enabled repair-render option then appears to be ignored.

## What Changes

- Scope each construction-plan request to its requested finding while retaining the configured business prompt and selected knowledge references.
- Add an explicit requested-work-item identity to the model input and instructions so the response cannot silently bind to another finding.
- Preserve strict identity validation and local fallback when a provider still returns an invalid identity.
- Keep fallback plans blocked from repair rendering, while allowing a confirmed valid remote `hold` plan to produce a clearly non-released preview as already intended by the current workflow contract.
- Add regression coverage for scoped request construction, identity mismatch fallback, and render eligibility.

## Capabilities

### Modified Capabilities

- `construction-plan-generation`: single-finding requests must not expose unrelated damage identities as generation context.
- `construction-plan-review-rendering`: confirmed valid remote plans may render previews; local fallback plans remain blocked.

## Impact

- `runtime/responses_construction_plan.py`: request payload/instruction assembly and semantic identity tests.
- `runtime/damage_workflow_gui.py`: render-gate diagnostics/tests only if needed to make fallback blocking explicit.
- `tests/test_responses_construction_plan.py` and `tests/test_damage_workflow_gui_contract.py`.
- Existing JSON/Markdown schema, evidence provenance, review gate, and construction release boundary remain unchanged.
