## Why

The construction-plan draft contract rejects an otherwise usable remote response when one limitation or checklist item is longer than 140 characters. The GUI then reports a generic generation failure and falls back, even though the content is a valid explanatory statement and the user explicitly needs the complete wording.

## What Changes

- Remove the per-item 140-character limit from construction-plan draft list text.
- Keep list cardinality limits and bounded top-level fields so the schema remains structured without rejecting long explanatory list entries.
- Add regression coverage proving a long `limitations` item survives strict draft validation and plan assembly.
- Preserve the existing human-review and non-release gates.

## Capabilities

### Modified Capabilities

- `construction-plan-generation`: long explanatory list entries must not cause an otherwise valid remote draft to fail local schema validation.

## Impact

- `runtime/construction_plan_schema.py`: relax the shared draft list-item string constraint.
- `tests/test_responses_construction_plan.py`: add a long-limitations regression test.
- Existing list item counts, top-level field limits, identity checks, provenance, and review gates remain unchanged.
