## 1. Reproduction and Contracts

- [x] 1.1 Add focused failing tests proving post-review construction generation is scheduled on a dedicated worker and duplicate starts are rejected.
- [x] 1.2 Add focused tests for thread-safe cumulative preview, stage/progress state, retry status, success, fallback-success, and terminal failure cleanup.

## 2. Observable Structured Generation

- [x] 2.1 Add a backward-compatible, credential-safe structured-output status callback for attempt, route, retry, and completion events.
- [x] 2.2 Propagate the optional status callback through `ResponsesConstructionPlanService` without changing timeout, retries, schemas, fallback, or persistence.

## 3. Non-Blocking Construction Worker

- [x] 3.1 Add `ConstructionPlanWorker` to own deterministic repair planning, active-v2 retrieval/context creation, remote generation, and unchanged local fallback.
- [x] 3.2 Replace synchronous `MainWindow.build_plan()` work with worker scheduling, signal wiring, duplicate-run guards, and retained worker lifetime.
- [x] 3.3 Add main-thread handlers for preparation, attempt/retry, cumulative character count, success, failure, and finished cleanup states.

## 4. Verification

- [x] 4.1 Run syntax checks and focused construction service/report-review/GUI contract tests with offscreen Qt.
- [x] 4.2 Run the maintained full test suite and verify the existing timeout/retry/fallback and artifact contracts remain unchanged.
- [x] 4.3 Strictly validate OpenSpec, record verification results, and document the real-provider responsiveness boundary.

## Verification record (2026-09-20)

- Root cause was verified against a live run: remote generation succeeded after about four minutes, but synchronous post-review execution made Windows report the Qt process as not responding.
- `py_compile` passed for the shared structured-output transport, construction service, and GUI module.
- Focused report/construction/report-review/GUI/RAG/settings suite: `139 passed`.
- Maintained full suite with offscreen Qt and third-party pytest plugin autoload disabled: `442 passed, 12 warnings`.
- The warnings are existing Docling/RapidOCR deprecation warnings in PDF conversion tests.
- Static boundary audit confirms `build_plan()` performs no retrieval, remote generation, fallback, or persistence; it starts `ConstructionPlanWorker` only.
- Static scope audit confirms the worker retains `timeout=180.0` and `retries=2`, and exposes neither a `stop()` method nor a cancellation signal.
- A delayed-worker offscreen Qt test confirms the main event loop processes queued GUI events while construction generation is still waiting in the background.
- Status events contain only fixed event/attempt/route fields; retry tests confirm provider failure text and credentials are not propagated.
- `openspec validate fix-construction-plan-gui-blocking --strict` passed.
- The pre-fix real-provider run established the failure mode and successful remote result. The patched source was verified with deterministic offscreen worker tests rather than issuing an additional paid/live provider request; the running application must be restarted to load the new worker implementation.
