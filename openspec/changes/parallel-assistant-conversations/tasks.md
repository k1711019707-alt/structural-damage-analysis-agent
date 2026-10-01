## 1. Parallel request state

- [x] 1.1 Replace the single global worker slot with request records keyed by request ID and conversation ID.
- [x] 1.2 Track pending streamed text independently for each active conversation.
- [x] 1.3 Prevent overlapping requests only within the same conversation while allowing requests in other conversations.

## 2. Dialog behavior

- [x] 2.1 Keep new-conversation creation and conversation selection enabled during generation.
- [x] 2.2 Render only the selected conversation's pending stream and preserve other conversations' streams in memory.
- [x] 2.3 Scope stop to the selected conversation and preserve silent cancellation semantics.
- [x] 2.4 Stop all workers safely during dialog close and ignore callbacks after closing.

## 3. Verification

- [x] 3.1 Add tests for creating and submitting a second conversation while the first streams.
- [x] 3.2 Add tests for concurrent delta isolation, switching, completion persistence, and per-conversation stop.
- [x] 3.3 Run assistant, GUI contract, layout, compile, and strict OpenSpec validation.
