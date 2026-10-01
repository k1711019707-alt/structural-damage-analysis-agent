## ADDED Requirements

### Requirement: Renderer preserves safe upstream failure diagnostics
The renderer SHALL record a concise diagnostic from a failed FHL child process, including an available HTTP status or service error, and MUST redact the configured API key before persisting or displaying the message.

#### Scenario: Images API returns an HTTP error
- **WHEN** the FHL child process exits nonzero and its output contains an HTTP error
- **THEN** the render result and manifest contain the HTTP error summary and return code instead of only a generic failure

#### Scenario: Child output contains the API key
- **WHEN** a failed child process includes the configured API key in stdout or stderr
- **THEN** the persisted and displayed message replaces the key with `<redacted>`

#### Scenario: Child process has no diagnostic output
- **WHEN** the FHL child process exits nonzero without usable stdout or stderr
- **THEN** the renderer records a generic failure with the process return code

### Requirement: GUI reports aggregate render outcomes truthfully
The GUI SHALL count successful, resumed, failed, and cancelled render results and SHALL select a terminal state that reflects the batch outcome.

#### Scenario: Every render item fails
- **WHEN** a completed render worker returns one or more results and all results have `failed` status
- **THEN** the GUI displays `修复渲染失败`, states that no image was generated, records the failure count and reason, and MUST NOT claim that all stages completed successfully

#### Scenario: Some render items fail
- **WHEN** a completed render worker returns both usable and failed results
- **THEN** the GUI displays a partial-completion state with usable and failed counts and preserves all usable output paths

#### Scenario: All render items are usable
- **WHEN** every completed result has `success` or `resumed` status
- **THEN** the GUI displays the normal completed state and reports the usable image count

### Requirement: Render event log identifies failed files and output location
The GUI SHALL log the resolved repair-render output directory when rendering begins and SHALL log each failed source filename with its safe failure reason.

#### Scenario: One item fails
- **WHEN** the render worker emits a failed result
- **THEN** the event log includes the source filename and safe diagnostic

#### Scenario: Rendering starts
- **WHEN** a non-empty repair render selection starts in the background worker
- **THEN** the event log includes the exact output directory used for generated images and the manifest
