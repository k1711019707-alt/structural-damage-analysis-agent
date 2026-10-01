## 1. Context routing

- [x] 1.1 Add explicit detection-context intent classification to `AssistantContextResolver`.
- [x] 1.2 Prevent non-explicit general questions from becoming ambiguous solely because multiple snapshots exist.
- [x] 1.3 Include bounded matching detection context when explicitly requested, without surfacing ordinary ambiguity or unresolved-batch warnings.

## 2. Answer orchestration

- [x] 2.1 Remove the terminal ambiguous-context response from `AssistantService.stream_answer()`.
- [x] 2.2 Update assistant instructions to combine relevant knowledge-base evidence with model understanding and allow normal no-hit fallback.
- [x] 2.3 Preserve active-v2 scope, web/model-prior routing, citations, warnings, trace IDs, cancellation, and read-only restrictions.

## 3. Verification

- [x] 3.1 Add a regression test for the screenshot-style general crack-classification question with multiple detection snapshots.
- [x] 3.2 Add tests for explicit detection questions and no-knowledge-base fallback behavior.
- [x] 3.3 Run focused assistant tests, GUI contract tests, and `openspec validate relax-assistant-context-routing --strict`.
