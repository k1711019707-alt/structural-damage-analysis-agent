## 1. OpenSpec

- [x] 1.1 Create proposal, design, capability delta, and verification tasks.

## 2. Implementation

- [x] 2.1 Remove the shared 140-character per-item constraint while retaining non-empty validation.
- [x] 2.2 Add regression coverage for a long limitation and preserve existing list-count validation.

## 3. Verification

- [x] 3.1 Run focused construction-plan tests: 69 passed.
- [x] 3.2 Run syntax checks and the maintained full suite: `py_compile` passed; 530 passed, 12 existing warnings.
- [x] 3.3 Validate this OpenSpec change strictly and record results: passed.

## Verification record (2026-09-21)

- `DraftListText` now validates non-empty trimmed text without a per-item character ceiling.
- A remote draft limitation longer than 140 characters was accepted and preserved through plan assembly.
- Existing review status and construction-release gates remain unchanged.
- `openspec validate fix-construction-plan-limitations-length --strict` passed.
