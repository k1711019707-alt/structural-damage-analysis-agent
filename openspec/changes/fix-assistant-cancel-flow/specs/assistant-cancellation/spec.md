## ADDED Requirements

### Requirement: User cancellation SHALL be a silent non-error outcome

When the user stops an active answer, the assistant SHALL treat the operation as an intentional cancellation rather than an API or model failure. The dialog SHALL remove the in-progress assistant content and SHALL NOT persist or display an assistant failure/cancellation message for that request.

#### Scenario: Stop before the first model delta

- **WHEN** the user clicks stop before any answer text is received
- **THEN** the service SHALL complete with cancelled status without raising `问答模型未返回文本`, and the conversation SHALL display no assistant message for that request

#### Scenario: Stop after partial text

- **WHEN** the user clicks stop after one or more answer deltas are visible
- **THEN** the dialog SHALL remove the partial assistant output and SHALL NOT persist it

#### Scenario: Provider error without user cancellation

- **WHEN** the provider fails and the user did not request cancellation
- **THEN** the existing failure message and API troubleshooting status SHALL remain available

### Requirement: Conversation navigation SHALL recover immediately after stop

The assistant dialog SHALL enable creation and selection of conversations immediately when stop is requested, without waiting for the provider stream or worker thread to terminate.

#### Scenario: Create a conversation while the stopped worker is winding down

- **WHEN** the user clicks stop and then clicks new conversation before the old worker emits `finished`
- **THEN** the dialog SHALL create and select the new conversation, and the old worker SHALL NOT write content or status into it

### Requirement: Worker callbacks SHALL remain bound to their originating request

Every answer worker SHALL carry a stable request identity and originating conversation ID. Completion, failure, delta, and finished callbacks SHALL be ignored for visible/persistent output after that request is cancelled or superseded.

#### Scenario: Late completion after cancellation

- **WHEN** a cancelled provider stream emits completion after the user has selected another conversation
- **THEN** no assistant message SHALL be saved to either the old or current conversation and the current conversation view/status SHALL remain unchanged

#### Scenario: Normal completion without navigation

- **WHEN** a non-cancelled worker completes normally
- **THEN** its answer and source manifest SHALL be saved only to the originating conversation
