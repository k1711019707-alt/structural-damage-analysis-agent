## 1. Contract tests and deterministic repair planning

- [x] 1.1 Add focused failing tests for deterministic method cards, stable repair identities, report-first facts, high-risk `RC-U01`, and null scale fields.
- [x] 1.2 Extend the mainline repair-plan models and planner with method-card, site-verification, report-basis, stop-work, citation, and optional image-path fields.
- [x] 1.3 Verify repair planning still requires a confirmed, integrity-valid, determinate v3 report and never uses detection geometry for grade, method, or quantity.

## 2. Hardened construction generation

- [x] 2.1 Add model-only construction draft schemas that exclude local status, release, provenance, evidence, method, scale, and review facts.
- [x] 2.2 Implement exact identity/order validation, scoped citation validation, deterministic enrichment, and local evidence/status/release computation for single and chunked generation.
- [x] 2.3 Replace the remote complete-plan path with locally assembled `construction-plan.v2` and preserve cumulative streaming callbacks plus current GenerationContext metadata.
- [x] 2.4 Update the local conservative fallback to use the same method cards, report basis, null scale fields, atomic persistence, and redacted failure audit.

## 3. Worker document, GUI, and packaging

- [x] 3.1 Replace the old Markdown renderer with one deterministic Traditional-Chinese worker/engineer document containing per-item evidence, report basis, procedures, figures/placeholders, quality, safety, references, review state, and limitations.
- [x] 3.2 Preserve the current GUI v3 confirmation flow, construction cumulative preview, retry behavior, active-v2 RAG routing, and final artifact opening.
- [x] 3.3 Add the main-project construction rules resource and package it through the maintained PyInstaller spec.

## 4. Legacy retirement and verification

- [x] 4.1 Remove the replaced simplified method mapping, model-controlled complete-plan generation, old Markdown renderer, unused compatibility parameters, and tests that permit evidence/method/release mutation.
- [x] 4.2 Add static checks proving no active code, config, test, resource, or package path depends on the external `施工方案生成` directory.
- [x] 4.3 Run syntax checks, focused repair/report/plan/RAG/GUI tests, the maintained full suite, and offscreen GUI workflow smoke tests.
- [x] 4.4 Validate packaged rule inclusion, strictly validate OpenSpec, record verification results and remaining real-provider/document-visual gaps, and mark completed tasks.

## Verification record (2026-09-20)

- `py_compile` passed for repair planning, construction schema/service, and GUI integration modules.
- Focused report/repair/plan/RAG/GUI/settings suite: `94 passed`.
- Maintained full suite with offscreen Qt and third-party pytest plugin autoload disabled: `436 passed, 12 warnings`.
- The warnings are existing Docling/RapidOCR deprecation warnings in PDF conversion tests.
- Static retirement scan found no legacy detection-only planner, model-owned complete-plan schema, `allow_additions`, worker-facing AI-diff renderer, or absolute dependency on the external migration source directory.
- `openspec validate migrate-harden-construction-plan-v2 --strict` passed.
- Packaged rule inclusion is covered by the maintained spec contract test. A real external-provider run, human review of a generated field document, and a rebuilt frozen executable remain release-environment verification boundaries rather than source-contract claims.
