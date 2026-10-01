# 工程问答助手

## ADDED Requirements

### Requirement: Assistant entry and dialog
The application SHALL expose a button named `问答助手` next to the existing `设置` control and open a reusable dialog containing a conversation list, message timeline, input editor, and send/stop/copy controls.

#### Scenario: Open and reuse assistant dialog
- **WHEN** the user clicks `问答助手`
- **THEN** the assistant dialog opens without closing the main workflow, restores the last selected conversation, and does not start a new conversation unless explicitly requested.

#### Scenario: Create a conversation
- **WHEN** the user clicks `新建对话`
- **THEN** a new conversation is persisted and selected, while existing conversations remain available.

#### Scenario: Send with Enter
- **WHEN** the message composer has non-empty content and the user presses `Enter` or the keypad Enter key without `Ctrl`
- **THEN** the dialog SHALL submit the message using the same guarded send path as the Send button.

#### Scenario: Insert a newline with Ctrl Enter
- **WHEN** the user presses `Ctrl+Enter` or `Ctrl` plus the keypad Enter key in the message composer
- **THEN** the composer SHALL insert a newline at the current cursor position and SHALL NOT submit the message.

### Requirement: Persistent conversation history
The application SHALL persist conversations and messages separately from GUI settings, preserving message text, role, timestamps, answer status, trace id, source manifest, and context snapshot references.

#### Scenario: Continue an old conversation
- **WHEN** the user selects an existing conversation
- **THEN** the complete available message history is loaded and the next question can continue with the recorded context snapshot.

#### Scenario: Report version changed
- **WHEN** a continued conversation references a report or plan hash that differs from the current artifact
- **THEN** the assistant SHALL warn that the old answer used an older version and SHALL ask whether to use the current version before silently changing the context.

### Requirement: Automatic source routing
The assistant SHALL automatically route each question across current detection evidence, historical detection records, current report, current plan, scoped knowledge base, web search, and model-prior knowledge without exposing source-mode selector controls in the normal composer.

#### Scenario: Current project question
- **WHEN** a question asks about the current detection, report, or construction plan
- **THEN** current structured evidence and matching artifacts SHALL be preferred before knowledge-base or model-prior context.

#### Scenario: Freshness-sensitive question
- **WHEN** a question contains a currentness intent such as `最新`, `现行`, `目前`, or a recent year
- **THEN** the assistant SHALL use the configured web-search provider when available and SHALL record URL, title, source domain, and access time.

#### Scenario: No sufficient evidence
- **WHEN** current artifacts and scoped retrieval are insufficient
- **THEN** the assistant MAY add model-prior knowledge only with an explicit unverified/freshness warning, and SHALL NOT fabricate a knowledge-base citation.

### Requirement: Historical detection resolution
The assistant SHALL resolve questions referring to a date, ordinal run, time period, source path, result path, image path, image name, or report/plan version through a read-only detection result catalog before generating an answer.

#### Scenario: Date query
- **WHEN** the user asks for results on a specific date
- **THEN** the resolver SHALL use the configured local timezone and query the full local-day interval against recorded detection timestamps.

#### Scenario: Ordinal query
- **WHEN** the user asks for the first, second, previous, or latest detection
- **THEN** the resolver SHALL order runs by recorded start time within the resolved project and source-path scope.

#### Scenario: Path query
- **WHEN** the user names an input path, result path, image path, or path alias
- **THEN** the resolver SHALL normalize Windows path spelling and match only registered catalog entries, never perform unrestricted arbitrary filesystem traversal.

#### Scenario: Ambiguous query
- **WHEN** multiple detection runs match the date, ordinal, or path constraints
- **THEN** the assistant SHALL present the candidate runs and request clarification instead of silently merging them.

### Requirement: Read-only tools and permissions
The assistant SHALL expose only read and retrieval tools for case evidence, reports, plans, scoped knowledge-base search, web search, source opening, and comparison. It SHALL NOT expose file mutation, deletion, rename, arbitrary command execution, configuration mutation, report overwrite, plan overwrite, knowledge-base write, or RAG activation tools.

#### Scenario: Mutation request
- **WHEN** a user asks the assistant to edit, delete, overwrite, execute, or activate something
- **THEN** the assistant SHALL refuse the action and explain that this assistant is read-only, while optionally offering a non-mutating explanation or draft in the chat.

### Requirement: Free-form answers with hidden provenance
The assistant SHALL permit free-form Markdown, plain text, tables, lists, comparisons, and explanations, while internally retaining source references, source mode, uncertainties, engineer-review status, and trace id without displaying provenance metadata in the conversation body.

#### Scenario: Conversation rendering
- **WHEN** an answer uses report, plan, evidence, knowledge-base, web, or model-prior material
- **THEN** the dialog SHALL display only the user and assistant message content and SHALL NOT render an `依据详情`, source list, or retrieval-warning section.

#### Scenario: Readable knowledge-base citations
- **WHEN** an answer cites a retrieved knowledge-base marker
- **THEN** the conversation SHALL show the verified indexed source filename and page in the answer body, adding chapter, section, or clause only when the cited chunk metadata unambiguously supplies it; internal marker and document ID SHALL remain in audit metadata, not the visible answer.

#### Scenario: Missing or historical citation metadata
- **WHEN** a stored answer contains an older knowledge-base marker or the retrieved citation has incomplete metadata
- **THEN** the dialog SHALL resolve the document ID against the active index where possible, omit unverifiable heading details, and show a neutral unverified-source label when a marker cannot be resolved; it SHALL NOT attribute content to a guessed standard or expose the opaque ID.

#### Scenario: Assistant identity
- **WHEN** the assistant describes its role to the user
- **THEN** it SHALL use the name `工程问答助手` and SHALL NOT call itself `只读工程问答助手`, while the read-only tool boundary remains enforced internally.

### Requirement: Streaming and cancellation
The assistant SHALL stream answer deltas and tool status events to the dialog, support cancellation, and persist only completed or explicitly cancelled message status without modifying report or plan artifacts.

#### Scenario: Cancel answer
- **WHEN** the user clicks stop during a response
- **THEN** the active request SHALL stop, the partial message SHALL be marked cancelled, and the conversation SHALL remain usable.
