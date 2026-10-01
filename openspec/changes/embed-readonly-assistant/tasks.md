# Tasks

## OpenSpec and contracts

- [x] Add and validate assistant proposal, requirements, design and task artifacts.
- [x] Add typed conversation, message, context snapshot, source reference and stream-event contracts.
- [x] Add focused tests for source precedence, no-answer/model-prior warnings, path normalization, date intervals, and ambiguous run resolution.

## Runtime assistant

- [x] Implement `runtime/assistant/` package with `ConversationStore`, `ContextResolver`, `SourceRouter`, `ReadonlyToolRegistry`, provider adapter and answer policy.
- [x] Implement SQLite conversation persistence under the user data root with atomic transaction boundaries and redacted diagnostics.
- [x] Implement read-only detection catalog adapters for run manifests, batch summaries, report artifacts, plan artifacts, image paths and evidence hashes.
- [x] Implement Responses streaming, cancellation and provider failure fallback without writing project artifacts.
- [x] Implement optional web-search provider contract with URL/title/domain/access-time provenance.

## GUI

- [x] Add `问答助手` button next to `设置` in the main window.
- [x] Add reusable assistant dialog with conversation list, new/continue/archive actions, free-form message rendering, stop, copy and source-details views.
- [x] Add automatic context resolution for date, ordinal, path, image, report and plan queries without source selector controls.
- [x] Preserve existing report/plan/knowledge-base GUI behavior and settings compatibility.

## Verification

- [x] Render indexed knowledge-base filenames and verified chapter/section/page inline; retain opaque markers only in audit metadata.
- [x] Resolve legacy conversation citations conservatively; verify streaming, final, copy, missing metadata, and unknown IDs with focused tests.
- [x] Run focused assistant tests and existing GUI/layout tests.
- [x] Exercise a real local context using a current report, plan, scoped knowledge base and historical query resolution.
- [x] Verify no assistant tool can write/delete/rename files, execute commands, alter settings, or activate RAG.
- [x] Validate OpenSpec change strictly; no OpenCode source was copied, so no third-party source attribution is required.
- [x] Hide internal source details and retrieval warnings from the conversation renderer while retaining audit metadata.
- [x] Rename the user-facing assistant identity from `只读工程问答助手` to `工程问答助手` and verify the dialog output.
- [x] Make `Enter` submit a message and `Ctrl+Enter` insert a newline in the assistant composer.
- [x] Add keyboard-event regression tests for Return, keypad Enter, Ctrl+Return, and Ctrl+keypad Enter.
