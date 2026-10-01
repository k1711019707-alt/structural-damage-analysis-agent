## Why

The GUI stores dedicated FHL Image Gen URL and API Key fields, but the current Node plugin ignores the `FHL_API_URL` and `FHL_API_KEY` environment values passed by the renderer and instead uses a hard-coded URL plus its global worker configuration. As a result, the settings screen is not the source of truth for FHL rendering and can silently use credentials different from those shown in the project GUI.

## What Changes

- Make an explicitly configured GUI `fhl_url` the effective endpoint for FHL repair rendering.
- Make an explicitly configured GUI `fhl_key` the effective bearer credential for FHL repair rendering without writing it to command-line arguments, logs, manifests, or the plugin's global config.
- Keep `responses_url`/`responses_key`, `fhl_url`/`fhl_key`, and `siliconflow_url`/`siliconflow_key` as independent provider-specific credential pairs with no cross-provider fallback.
- Preserve compatibility with existing plugin worker configuration only when the GUI FHL key is empty.
- Validate URL shape and missing-credential behavior before starting a paid image request, with bounded and redacted diagnostics.
- Keep FHL image generation and editing on the Images API; do not route FHL rendering through Responses.

## Capabilities

### New Capabilities

- `fhl-gui-credential-routing`: Defines authoritative GUI-to-FHL endpoint and credential routing, provider isolation, compatibility fallback, and secret-safe diagnostics.

### Modified Capabilities

None.

## Impact

- Affects `runtime/fhl_repair_renderer.py`, the project-owned FHL launcher/adapter, and focused renderer, settings, GUI, and packaging tests.
- May require a project-owned wrapper or patched packaged script contract so source and frozen builds behave identically.
- Does not change Responses report/plan services, SiliconFlow request behavior, FHL prompt/size policy, or repair-render output formats.
- Existing user settings remain readable; no automatic copying between general, FHL, or SiliconFlow credentials is introduced.
