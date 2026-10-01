## 1. OpenSpec and request scoping

- [x] 1.1 Create the proposal and design for the identity-scoped construction request and render boundary.
- [x] 1.2 Define regression cases for full-context leakage, fallback provenance, and render eligibility.

## 2. Implementation

- [x] 2.1 Replace full `GenerationContext.prompt` usage in construction requests with profile-only instructions plus request-scoped context.
- [x] 2.2 Add explicit requested identity fields and preserve strict local identity validation.
- [x] 2.3 Keep local-fallback render blocking and improve any ambiguous diagnostic text without changing safety gates.

## 3. Verification

- [x] 3.1 Run focused construction-plan and GUI contract tests: 70 passed.
- [x] 3.2 Run syntax checks and the maintained test suite: `py_compile` passed; 529 passed, 12 warnings.
- [x] 3.3 Validate this OpenSpec change strictly: passed.

## Verification record (2026-09-21)

- The construction request now uses `GenerationContext.profile_prompt` plus the scoped report/repair line, rather than the full-batch `GenerationContext.prompt`.
- Each request carries `requested_work_item_identities`; strict local identity validation remains unchanged.
- Confirmed remote `hold` plans retain preview eligibility; any `local_fallback` work item remains blocked and the GUI explains that the remote draft must be regenerated.
- Focused construction/GUI tests: 70 passed.
- Maintained suite: 529 passed, 12 existing PDF deprecation warnings.
- `openspec validate fix-construction-plan-identity-and-render-flow --strict` passed.
