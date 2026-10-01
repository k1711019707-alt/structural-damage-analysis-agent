## ADDED Requirements

### Requirement: Multiple conversations SHALL generate independently

The assistant dialog SHALL allow an answer request to continue in one conversation while the user creates, selects, and submits a request in another conversation. Each request SHALL have its own worker, stop event, request identity, and originating conversation identity.

#### Scenario: Create another conversation while the first answer streams

- **WHEN** conversation A is receiving streamed answer text
- **THEN** the user SHALL be able to create conversation B and submit a question in B without stopping A

#### Scenario: Two conversations stream concurrently

- **WHEN** conversation A and conversation B both have active requests
- **THEN** each streamed delta SHALL be rendered only in its originating conversation and each completed answer SHALL be persisted only to that conversation

### Requirement: Conversation navigation SHALL remain available during generation

The dialog SHALL keep new-conversation creation and conversation selection enabled while any request is running. The composer and send action SHALL be scoped to the selected conversation and SHALL not be globally disabled by another conversation's worker.

#### Scenario: Switch to an active conversation

- **WHEN** the user selects conversation A while conversation B is generating
- **THEN** the dialog SHALL show A's stored messages and A's pending answer, if any, without cancelling B

#### Scenario: Submit in a newly selected conversation

- **WHEN** the user selects conversation B and sends a question while A is generating
- **THEN** B SHALL start its own worker and A SHALL continue generating independently

### Requirement: Stop SHALL apply to one conversation only

The stop control SHALL cancel only the active request associated with the selected conversation. Requests belonging to other conversations SHALL continue, and their late callbacks SHALL remain isolated.

#### Scenario: Stop selected conversation

- **WHEN** the user stops conversation B while conversation A is still generating
- **THEN** B's pending answer SHALL be discarded according to the silent-cancellation contract and A SHALL continue unchanged

### Requirement: Window close SHALL stop all active workers safely

When the assistant dialog closes, it SHALL request cancellation for every active worker and SHALL delay final destruction until all worker threads have finished. No worker callback SHALL update a destroyed dialog.

#### Scenario: Close with multiple active requests

- **WHEN** the user closes the dialog while multiple conversations are generating
- **THEN** all active requests SHALL receive stop signals and the dialog SHALL close only after their workers finish
