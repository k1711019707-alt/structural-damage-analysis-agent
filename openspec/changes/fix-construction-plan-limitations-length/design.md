## Context

`ConstructionPlanDraft` uses `DraftListText` for limitations, checks, controls, materials, and procedure lists. `DraftListText` currently applies `max_length=140` to every item. The screenshot failure is a Pydantic `string_too_long` error for `limitations.0`, so the failure occurs after the provider response and before the draft can be assembled into the reviewable plan.

## Decisions

### Remove only the per-item character ceiling

`DraftListText` will retain whitespace stripping and a non-empty requirement, but will no longer impose a character maximum. This applies consistently to all structured list fields using the alias and avoids a special case where limitations behave differently from other explanatory fields.

### Keep structural bounds

The schema will retain `max_length` on bounded scalar fields such as `scope`, `executive_summary`, method names and rationales, as well as `max_length` on each list field's item count. These bounds protect request shape and provider output volume without truncating user-visible explanatory text.

### Preserve review boundaries

Long text remains draft content. It does not change identity validation, provenance, `pending_engineer_review`, construction release state, or rendering eligibility. The remote draft must still pass the complete local schema and evidence checks.

## Verification

1. Validate a construction draft containing a limitation longer than 140 characters.
2. Generate a plan through the service with that draft and assert the complete limitation is retained.
3. Run construction-plan focused tests, the full suite, `py_compile`, and strict OpenSpec validation.
