## ADDED Requirements

### Requirement: Post-review construction generation does not block the GUI thread
The application SHALL execute repair-plan preparation, knowledge retrieval, remote construction-plan generation, validation, persistence, and fallback outside the Qt GUI thread after a damage report is confirmed.

#### Scenario: Confirmed report starts construction generation
- **WHEN** the user confirms a valid damage report
- **THEN** the application starts one construction-plan worker and returns control to the Qt event loop without executing the remote request on the GUI thread

#### Scenario: Duplicate start while generation is active
- **WHEN** construction-plan generation is already running and another start is requested
- **THEN** the application SHALL keep the existing worker and SHALL NOT start a second generation request

### Requirement: Worker communicates through Qt signals
The construction-plan worker SHALL communicate stage, progress, cumulative text, repair-plan summary, final plan, failure, and lifecycle completion through Qt signals, and SHALL NOT mutate GUI widgets directly.

#### Scenario: Streaming content arrives
- **WHEN** the provider emits cumulative structured-output text
- **THEN** the worker forwards it to the main thread and the current-file preview displays the latest cumulative content while the window remains responsive

#### Scenario: Generation succeeds
- **WHEN** the worker persists a valid construction plan
- **THEN** the GUI displays the final plan status, advances to the construction stage, and clears the active worker state

#### Scenario: Generation fails outside the existing local fallback
- **WHEN** the worker reports a terminal failure
- **THEN** the GUI preserves the confirmed report, displays a retryable failure state, and clears the active worker state

### Requirement: Construction generation status is truthful and observable
The GUI SHALL replace the stale report-review state with construction-plan preparation and generation status, including request-attempt/retry and cumulative-character information when available.

#### Scenario: Worker starts preparing context
- **WHEN** the construction worker starts
- **THEN** the overall progress and stage labels identify construction-plan preparation rather than report review

#### Scenario: Provider retries an existing request
- **WHEN** the existing structured-output retry loop schedules another attempt
- **THEN** the GUI receives a credential-safe status message identifying the attempt or retry state

### Requirement: Existing wait and fallback contracts remain unchanged
The change MUST preserve the current construction timeout, retry count, compatible-provider routing, strict draft validation, local fallback behavior, active-v2 RAG metadata, and final artifact formats.

#### Scenario: Remote provider is slow but eventually succeeds
- **WHEN** generation takes several minutes within the existing timeout and retry behavior
- **THEN** the GUI remains responsive and the successful remotely drafted, locally assembled plan is persisted normally

#### Scenario: Existing fallback condition occurs
- **WHEN** the current service exhausts its existing remote behavior and raises an error
- **THEN** the worker invokes the unchanged local conservative fallback and reports the resulting plan through the success path

### Requirement: No construction cancellation or new bounded wait
The application SHALL NOT add a construction-plan cancellation control or introduce a new shorter deadline, retry cap, or bounded fallback wait as part of this change.

#### Scenario: Construction generation remains active
- **WHEN** the existing provider request is still running
- **THEN** the worker continues under the existing timeout and retry configuration while the GUI remains responsive
