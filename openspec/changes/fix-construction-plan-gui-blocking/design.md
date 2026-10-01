## Context

The initial recognition and report-generation stage already runs in `AutomaticWorkflowWorker`, but that worker intentionally finishes at the human-review gate. After confirmation, `QTimer.singleShot` invokes `MainWindow.build_plan()` on the GUI thread, where retrieval, remote streaming, validation, persistence, and fallback execute synchronously. A live request remained successful but occupied the GUI thread for about four minutes, during which Windows reported the application as not responding and the displayed workflow state remained at 50%.

The construction service already emits cumulative text and preserves strict validation, retry, fallback, RAG, and persistence contracts. The missing boundary is thread-safe orchestration after report confirmation.

## Goals / Non-Goals

**Goals:**

- Keep the Qt event loop responsive throughout construction-plan preparation and remote generation.
- Reuse the established `QThread` and Qt Signal pattern used by recognition, knowledge synchronization, and report generation.
- Make preparation, connection/attempt, streaming, local assembly, success, and failure states visible.
- Preserve exact construction-plan generation semantics and artifacts.

**Non-Goals:**

- No cancellation capability for construction-plan generation.
- No new deadline, shorter timeout, retry limit, or bounded local-fallback policy.
- No construction schema, method-card, RAG, provider, persistence, or review-gate changes.
- No changes to the concurrently planned human-friendly report-review form.

## Decisions

### Dedicated `ConstructionPlanWorker`

Create a `QThread` subclass that receives immutable snapshots of the recognition summary, output path, API configuration, and settings. It owns repair-plan construction, RAG retrieval, `GenerationContext`, remote generation, fallback, and persistence. This follows the existing worker pattern and avoids calling widgets from the worker.

Keeping the stage in `build_plan()` with manual event pumping was rejected because nested `processEvents()` calls create reentrancy hazards and still leave network work on the GUI thread. Reusing `AutomaticWorkflowWorker` was rejected because it has already completed at the human-review boundary and restarting a `QThread` instance would conflate two lifecycle phases.

### Signal-only UI communication

The worker emits stage/progress, cumulative generation text, repair-plan summary, final construction plan, failure, and finished lifecycle signals. The main window alone mutates widgets. `MainWindow.build_plan()` becomes a scheduler and guard rather than an implementation of the generation pipeline.

### Optional transport status callback

Add a backward-compatible optional status callback to the shared structured-output transport and construction service. It reports request attempt, compatible endpoint route, retry scheduling, and successful completion without exposing credentials. Construction worker converts these callbacks to Qt signals. Existing callers that do not provide the callback retain current behavior.

### Preserve waiting and fallback behavior

The worker keeps `timeout=180.0`, `retries=2`, existing compatible-endpoint routing, and existing local fallback. The change makes waiting non-blocking and observable; it does not shorten or cap it beyond current behavior.

## Risks / Trade-offs

- [Concurrent report-review UI work touches the same module] -> Keep edits limited to worker definitions, main-window worker state, signal handlers, and `build_plan`; do not modify dialog controls.
- [Qt object lifecycle could allow a worker to be garbage-collected] -> Store the worker on the main window and clear it only from the thread's `finished` signal.
- [Starting a second plan while one is active could duplicate artifacts] -> Guard on `plan_worker.isRunning()` and keep `_plan_generation_scheduled` true until completion/failure.
- [Worker exceptions could leave the UI locked] -> Route every terminal path through explicit success/failure handlers and a shared finished cleanup handler.
- [Status callbacks could expose provider errors] -> Emit fixed stage messages and existing redacted error handling only; never include API keys.

## Migration Plan

1. Add failing tests proving `build_plan()` schedules a worker and returns without executing generation on the caller thread.
2. Add optional status callbacks and focused transport tests.
3. Implement the worker and main-window signal lifecycle.
4. Run focused offscreen Qt tests, construction service tests, full suite, and strict OpenSpec validation.

Rollback consists of reverting the worker/status callback changes; persisted schemas and artifacts require no migration.

## Open Questions

None. The user explicitly excluded cancellation and bounded fallback waiting.
