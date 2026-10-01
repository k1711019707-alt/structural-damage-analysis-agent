## Context

`AssistantService.stream_answer()` currently validates that the accumulated answer is non-empty before it computes `cancelled`. If the stop flag is set before a text delta arrives, the service raises `RuntimeError("问答模型未返回文本")`. `AssistantDialog` then stores this as a failed assistant message and shows an API warning even though the provider is healthy.

The dialog also disables the conversation list and new-conversation button for the worker's whole lifetime. Simply re-enabling them on stop would create a second bug because completion and failure slots currently use mutable `current_conversation`; a late callback could be persisted into the newly selected conversation.

## Goals / Non-Goals

**Goals:**

- Make user cancellation silent and non-error.
- Remove partial assistant output and persist no assistant record for a cancelled request.
- Allow immediate new-conversation creation and navigation after stop.
- Bind asynchronous callbacks to the originating request and conversation.
- Preserve normal provider failure reporting when no cancellation was requested.

**Non-Goals:**

- Do not implement concurrent answer generation in several conversations.
- Do not attempt provider-specific server-side request cancellation.
- Do not delete the user's submitted question.
- Do not change API endpoints, RAG routing, or persistent database schema.

## Decisions

### 1. Return cancellation before empty-answer validation

After the stream loop exits, `stream_answer()` checks the stop event before rejecting an empty answer. A cancelled call returns an empty answer plus a normal manifest with `cancelled=true`. Provider failures still raise when no stop was requested.

### 2. Treat partial output as ephemeral

The dialog may render deltas while generation is active, but cancellation clears `_stream_text`, re-renders stored messages, and does not call `ConversationStore.add_message()` for the assistant. This implements “停止后什么都不显示” without deleting the already submitted user question.

### 3. Assign request and conversation identities

Each `AssistantWorker` receives a generated request ID and the originating conversation ID. Signals include these identities. Slots accept output only when it belongs to the active request. Normal completion is persisted to the origin conversation even if rendering state later changes; cancelled or stale callbacks are discarded.

### 4. Unlock navigation at stop time

`_stop()` disables further stop clicks, keeps send disabled until the worker ends, and immediately enables the new-conversation button and conversation list. This permits navigation without permitting concurrent generation. `_worker_finished()` performs the final send-button reset only for the matching worker.

## Risks / Trade-offs

- [Risk] The remote request may continue briefly after local cancellation. → Ignore its deltas and callbacks; no server-side cancellation is claimed.
- [Risk] A stale callback could alter the status of a new conversation. → Gate every callback by request ID and avoid status updates for cancelled/stale requests.
- [Risk] Hiding cancellation removes an audit message from the conversation. → Cancellation remains a transient UI action; no incomplete assistant content is represented as durable evidence.

## Migration Plan

No database migration is needed. Deploy the runtime and tests together. Existing historical `cancelled` messages remain readable; new cancellations create no assistant message.
