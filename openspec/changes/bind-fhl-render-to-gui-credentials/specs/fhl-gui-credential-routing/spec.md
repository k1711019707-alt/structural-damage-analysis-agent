## ADDED Requirements

### Requirement: GUI FHL configuration is authoritative for explicit FHL rendering
The system SHALL use the saved GUI `fhl_url` and `fhl_key` as the endpoint and bearer credential whenever a non-empty FHL key is configured, and MUST NOT allow plugin-global settings to override either value.

#### Scenario: Explicit GUI FHL configuration is used
- **WHEN** FHL is selected and the saved GUI configuration contains a non-empty FHL URL and key
- **THEN** the repair-render request is sent to the normalized GUI FHL edit endpoint with the GUI FHL key
- **THEN** no plugin-global credential is read or modified for that request

### Requirement: Provider credentials remain isolated
The system MUST treat Responses, FHL Image Gen, and SiliconFlow URL/key pairs as independent provider credentials and MUST NOT copy, infer, or fall back across providers.

#### Scenario: FHL rendering starts with all provider credentials configured
- **WHEN** the selected repair-render provider is FHL
- **THEN** only `fhl_url` and `fhl_key` are used for the image request
- **THEN** `responses_url`, `responses_key`, `siliconflow_url`, and `siliconflow_key` are not consulted

#### Scenario: General Responses credentials exist but FHL key is absent
- **WHEN** FHL is selected, the GUI FHL key is empty, and a Responses key is configured
- **THEN** the system does not use the Responses key as an FHL credential

### Requirement: FHL GUI route follows the Images API edit contract
The system SHALL submit a multipart Images API edit request using the source image, repair prompt, `gpt-image-2`, one output, the tested 4:3 size, PNG output, and base64 response format, and MUST NOT route repair rendering through Responses.

#### Scenario: Submit one repair-render source image
- **WHEN** an image is rendered with an explicit GUI FHL key
- **THEN** the request targets the normalized `/v1/images/edits` endpoint and contains the documented image-edit fields
- **THEN** a returned base64 image is persisted in the repair-render output directory and recorded as successful

#### Scenario: Provider returns no base64 image
- **WHEN** FHL returns HTTP success without a usable base64 image
- **THEN** the system records a failed result and does not fabricate an output path

### Requirement: Invalid FHL GUI configuration fails before network access
The system SHALL validate explicit FHL URL and credential input before opening a network request and SHALL surface an actionable provider-specific failure.

#### Scenario: Explicit FHL URL is invalid
- **WHEN** the GUI FHL key is non-empty but the configured FHL URL has no supported HTTP endpoint form
- **THEN** the item fails before network access with an FHL configuration message

### Requirement: FHL failures and credentials are handled safely
The system MUST omit the FHL key from process arguments, plugin-global configuration mutations, exported settings, render manifests, GUI logs, and surfaced errors. Retryable FHL failures SHALL be retried a bounded number of times and diagnostics SHALL remain bounded.

#### Scenario: Provider error contains the configured FHL key
- **WHEN** an HTTP error body or exception includes the configured FHL key
- **THEN** the persisted and displayed diagnostic replaces it with `<redacted>`

#### Scenario: FHL returns a retryable gateway failure
- **WHEN** FHL returns HTTP 429, 502, 503, 504, or 524
- **THEN** the request is attempted no more than four total times and the final failure records the status without exposing the key

### Requirement: Legacy plugin configuration remains a compatibility fallback
The system SHALL retain the existing Node plugin route when no GUI FHL key is configured, while keeping that fallback isolated from Responses and SiliconFlow credentials.

#### Scenario: Legacy installation has no GUI FHL key
- **WHEN** FHL is selected and the GUI FHL key is empty
- **THEN** the renderer may use the existing plugin worker configuration
- **THEN** it does not substitute any other GUI provider key

