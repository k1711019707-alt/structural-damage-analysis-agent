## ADDED Requirements

### Requirement: SiliconFlow image-edit request follows the documented model contract
The system SHALL call the configured SiliconFlow `/images/generations` endpoint with `Qwen/Qwen-Image-Edit-2509`, the repair prompt, and the source image as a base64 data URL, and MUST NOT send `image_size` for this model.

#### Scenario: Submit one source image for repair rendering
- **WHEN** the selected provider is SiliconFlow and a repair item is rendered
- **THEN** the system sends JSON containing `model`, `prompt`, and `image` to `/images/generations` with bearer authentication
- **THEN** the JSON does not contain `image_size`

### Requirement: SiliconFlow results are persisted before temporary URLs expire
The system SHALL download a successful `images[0].url` response into the existing repair-render output directory and SHALL record the resulting local path in `render_manifest.json`.

#### Scenario: Provider returns a downloadable image URL
- **WHEN** SiliconFlow returns a successful response with `images[0].url`
- **THEN** the system downloads a non-empty image, atomically activates the local output, and records a successful result with the local path

#### Scenario: Provider response has no image URL
- **WHEN** SiliconFlow returns HTTP success without a usable `images[0].url`
- **THEN** the system records a failed result and does not fabricate an output path

### Requirement: Repair-render provider configuration is explicit and backward compatible
The GUI SHALL expose a repair-render provider selector and separate SiliconFlow URL, API Key, and model fields while preserving existing FHL settings. Existing settings without the new fields SHALL continue to select FHL.

#### Scenario: Existing settings are loaded
- **WHEN** `gui_settings.json` contains only the existing FHL fields
- **THEN** the system fills SiliconFlow defaults and continues using FHL

#### Scenario: SiliconFlow is selected
- **WHEN** the user saves SiliconFlow as the repair-render provider
- **THEN** the background repair-render worker uses the SiliconFlow renderer and leaves the FHL credentials unchanged

### Requirement: SiliconFlow credentials and failures are handled safely
The system MUST omit the SiliconFlow API Key from exported settings, render manifests, GUI logs, and surfaced error messages. HTTP and response diagnostics SHALL remain bounded and actionable.

#### Scenario: Export settings containing a SiliconFlow key
- **WHEN** settings are exported with secret redaction enabled
- **THEN** the exported `siliconflow_key` is empty

#### Scenario: Provider error contains the configured key
- **WHEN** a failed response or exception contains the SiliconFlow API Key
- **THEN** the persisted and displayed diagnostic replaces it with `<redacted>`

### Requirement: Missing SiliconFlow credentials fail without a network request
The system SHALL return a failed render result before network access when SiliconFlow is selected without an API Key.

#### Scenario: SiliconFlow key is empty
- **WHEN** a repair item is submitted with provider `siliconflow` and an empty key
- **THEN** the item fails with an actionable configuration message and no HTTP request is made
