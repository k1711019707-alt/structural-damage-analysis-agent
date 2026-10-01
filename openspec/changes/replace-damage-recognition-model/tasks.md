## 1. Model migration

- [x] 1.1 Record and back up the legacy formal checkpoint outside the active project model directory.
- [x] 1.2 Copy the P2 checkpoint to `models/best.pt` and update deployment integrity/provenance metadata.
- [x] 1.3 Add the project-contained P2 architecture and portable training configuration.

## 2. Runtime contracts

- [x] 2.1 Strengthen formal-model validation for exact class order and P2 strides.
- [x] 2.2 Add focused tests for the new manifest, model contract, and external-path prohibition.
- [x] 2.3 Verify the existing GUI and structured `damage-finding.v1` flows use the replaced default model.

## 3. Integration verification and cleanup

- [x] 3.1 Run focused runtime, GUI, training, packaging, and model tests.
- [x] 3.2 Run real GPU inference through the production runtime on representative images.
- [x] 3.3 Validate package inputs and reproduction bundle metadata against the new checkpoint.
- [x] 3.4 Remove legacy model references and verify the project has no dependency on the external delivery directory.
- [x] 3.5 Validate the OpenSpec change strictly and record completed evidence.

## 4. GUI crash hardening

- [x] 4.1 Remove nested Qt event processing from workflow-stage and streamed-generation GUI slots.
- [x] 4.2 Add regression coverage proving queued generation updates cannot re-enter the active update slot.
- [x] 4.3 Run focused GUI tests, an offscreen GUI smoke test, and strict OpenSpec validation.
- [x] 4.4 Preserve immediate preview painting for the legacy synchronous manual-generation entry points without nested event processing.

## Verification evidence

- New formal model: 21,958,024 bytes; SHA-256 `3510277ee35bf253de4328c244bdaf2fbdb30d92868b06fbb7b2fd5bef582f4d`.
- Focused model/runtime/GUI/training/packaging suite: 115 passed.
- Real integration: CUDA `cuda:0`, three runtime images succeeded, GUI worker completed two images and wrote its batch summary.
- Full suite: 398 passed, one pre-existing adaptive-layout assertion failed because the 1680x980 vertical scrollbar maximum was 105 instead of at most 12.
- Legacy run moved out of the active project to `artifacts/model-migration-20260919/legacy-damage-module-run`; no automatic runtime fallback remains.
- Crash evidence: Windows Application Error at 2026-09-19 20:42:09 reported `Qt6Gui.dll` with exception `0xc00000fd` (native stack overflow), after detection and generated artifacts had completed successfully.
- Crash hardening: all four production GUI-slot calls to `QApplication.processEvents()` were removed; the queued-update regression and GUI contract suite passed (`32 passed`).
- Qt stream stress: 5,000 worker-thread updates produced a complete 5,000-character preview, exited normally, and added zero Python/Qt Application Error events.
- Post-fix full suite: 399 passed, with two unrelated failures: the pre-existing adaptive-layout maximum (105 versus 12), and a read-only catalog isolation assertion that included the real user snapshot `c0642683-b29c-4e97-8642-a46814dbd270` alongside its temporary fixture.
- Final focused GUI/visual/model/runtime suite: 90 passed. Legacy manual report and plan streaming now repaint only the preview controls; its regression test verifies repaint is called without dispatching a queued unrelated event. A temporary Qt event-filter test probe caused an unrelated native heap error during investigation; the probe was removed and the final suite reran successfully.
