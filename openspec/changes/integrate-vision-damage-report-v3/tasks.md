## 1. Data contracts and report core

- [x] 1.1 Add report v3 models for minimal detection hints, visual evidence, fixed damage levels, AI-only drafts, local provenance, human review, and integrity state.
- [x] 1.2 Add exact finding-identity validation, stable input-order normalization, confirmation validation, and legacy-version diagnostics.
- [x] 1.3 Add bounded image normalization/encoding with metadata-free audit records and hard request budgets.

## 2. Generation service integration

- [x] 2.1 Port visual report generation onto the mainline cumulative streaming Responses/Chat service without changing its callback contract.
- [x] 2.2 Make single and chunked generation construct system-owned v3 reports, reject correspondence mismatches, recompute overall severity, and persist atomic audit artifacts.
- [x] 2.3 Replace geometry-derived local fallback content with complete `undetermined` findings and redacted failure provenance.
- [x] 2.4 Preserve active-v2 RAG hierarchy, warnings, source mode, route diagnostics, and manifest fields through report generation.

## 3. GUI and downstream workflow

- [x] 3.1 Emit minimal batch-summary findings while retaining original and recognition-overlay paths for visual generation.
- [x] 3.2 Add report editing, reviewer input, save-pending, and validated human-confirmation controls to the current mainline GUI.
- [x] 3.3 Stop the automatic workflow at human review and gate repair/construction-plan generation on a confirmed, valid v3 report.
- [x] 3.4 Adapt repair planning, construction-plan schemas/services, Markdown, DOCX mapping, and manifests to scale-free v3 fields.
- [x] 3.5 After the v3 focused tests pass, remove the replaced v1 geometric report models, pixel/area grading fallback, legacy generation branch, and unreviewed downstream path from the main project.

## 4. Compatibility and verification

- [x] 4.1 Add or update focused tests for minimal prompts, visual budgets, strict drafts, system audit fields, identity mismatch blocking, Chinese alias normalization, chunked severity, fallback, and confirmation.
- [x] 4.2 Add or update GUI/RAG/downstream tests proving active-v2 metadata and cumulative streaming behavior are preserved.
- [x] 4.3 Add a static verification that active main-project code and configuration contain no reference or runtime dependency on the external `分析报告生成` directory.
- [x] 4.4 Run syntax checks, focused report/GUI/downstream tests, the maintained full suite, and offscreen GUI smoke checks.
- [x] 4.5 Strictly validate the OpenSpec change, record any remaining external-provider or document-visual verification gaps, and mark completed tasks.

## Verification record

- Maintained suite: `430 passed, 12 warnings` with the project Conda interpreter and Qt offscreen mode.
- Focused report/GUI/downstream regression: `153 passed` after the final contract updates.
- OpenSpec: `openspec validate integrate-vision-damage-report-v3 --strict` passed.
- Static dependency test confirms active project code/config has no absolute reference to the external optimization directory.
- Remaining external checks: a real provider call was not made, and generated DOCX pages were not manually reviewed in Word. The deterministic mapping, image-slot behavior, atomic output, and offscreen GUI layout are covered by automated tests.
