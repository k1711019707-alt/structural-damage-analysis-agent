## 1. Cancellation semantics

- [x] 1.1 Return a cancelled manifest before empty-answer validation in `AssistantService`.
- [x] 1.2 Discard partial assistant text and persist no assistant message on cancellation.
- [x] 1.3 Preserve provider failure reporting when no cancellation was requested.

## 2. Conversation lifecycle

- [x] 2.1 Bind workers and callbacks to stable request and originating conversation IDs.
- [x] 2.2 Re-enable new-conversation creation and conversation selection immediately after stop.
- [x] 2.3 Prevent cancelled or stale callbacks from changing a newly selected conversation or its status.

## 3. Verification

- [x] 3.1 Add service tests for cancellation before and after text deltas.
- [x] 3.2 Add dialog tests for silent cancellation, immediate new conversation, and late callback isolation.
- [x] 3.3 Run assistant, GUI contract, layout tests, compile checks, and strict OpenSpec validation.
