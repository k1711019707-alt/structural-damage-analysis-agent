## Context

The previous cancellation repair introduced request and conversation IDs but retained `AssistantDialog.worker: AssistantWorker | None` as a single global slot. `_send()` rejects any request while that slot is non-null, disables the new/switch controls, and `_stop()` can only address that one worker. This makes the UI effectively single-conversation even though conversation data is persisted independently.

## Goals / Non-Goals

**Goals:**

- Support concurrent answer generation in multiple conversations.
- Keep streamed text, completion, failure, and cancellation isolated by request and conversation.
- Allow navigation and new conversation creation while any request is active.
- Show the pending stream for the currently selected conversation only.
- Stop one selected request without affecting others.
- Close safely by cancelling and joining all workers.

**Non-Goals:**

- Do not change the provider/API or knowledge retrieval service.
- Do not alter the SQLite schema; pending text remains in memory until completion.
- Do not add a global concurrency limit in this change; each user request may create one worker.

## Decisions

### 1. Replace the global worker with request records

Maintain a dictionary keyed by `request_id`, storing worker, stop event, conversation ID, and current streamed text. Maintain a second mapping from conversation ID to its active request ID so one conversation cannot submit overlapping messages while another conversation can run.

Keep a compatibility `worker` property only if existing tests or close handling need it; new logic must use the request dictionary as the source of truth.

### 2. Render pending text by conversation ID

Store pending deltas per conversation/request. `_render_messages()` reads the pending value for `current_conversation`; switching conversations therefore changes the visible pending answer without cancelling or losing the other stream.

### 3. Scope controls to the selected conversation

The new-conversation and conversation-list controls remain enabled whenever the dialog is open. Send is disabled only when the selected conversation already has an active request. Stop is enabled only when the selected conversation has an active request. New conversations can be created even while all existing conversations are busy.

### 4. Gate every callback by request identity

Delta, completed, failed, and finished slots receive request ID and conversation ID. Cancelled or unknown request IDs are ignored for rendering and persistence. Completion writes to the originating conversation through `ConversationStore`, then refreshes the visible view only if that conversation is selected.

### 5. Close through a pending-worker counter

On close, set the stop event for every active request, mark the dialog as closing, and ignore visible updates. Connect each worker's `finished` signal to remove its record. Call `accept/close` only after the collection is empty.

## Risks / Trade-offs

- [Risk] Several simultaneous remote requests increase API usage. → This is explicitly requested multi-conversation behavior; each request remains user initiated and auditable.
- [Risk] A provider may not stop immediately. → Ignore late events by request identity and wait for worker termination on close.
- [Risk] The selected conversation can change during a callback. → Persist by originating conversation ID and render only when IDs match.

## Migration Plan

No data migration is required. Existing conversations and historical messages remain compatible. Replace the dialog runtime and run focused GUI tests; rollback is a source revert.
