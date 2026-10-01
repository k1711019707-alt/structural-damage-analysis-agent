## Context

`RepairRenderWorker` already passes `fhl_key` and `fhl_url` from the unified GUI settings into `FhlRepairRenderer`. The renderer forwards those values as environment variables to the FHL Node process, but FHL Image Gen `0.2.1` reads its worker key from `~/.codex/fhl-image-gen-config.json` and uses a hard-coded `https://www.fhl.mom` root. The packaged `fhl_runner.mjs` additionally copies `FHL_API_KEY` into the global plugin config, which mutates state outside the application and still does not honor the GUI URL.

The application has three deliberately separate provider configurations: Responses for report/plan generation, FHL Images for repair rendering, and SiliconFlow for alternate repair rendering. Credentials must never flow across these boundaries.

## Goals / Non-Goals

**Goals:**

- Make non-empty GUI `fhl_url` and `fhl_key` authoritative for FHL repair-render requests.
- Keep Responses, FHL, and SiliconFlow URL/key pairs isolated.
- Use FHL Images API multipart edit requests with `gpt-image-2` and existing 4:3 behavior.
- Preserve the existing Node plugin as a compatibility fallback for legacy installations without a GUI FHL key.
- Keep secrets out of command arguments, logs, manifests, and plugin-global configuration.
- Produce identical behavior in source and frozen applications.

**Non-Goals:**

- Do not route FHL rendering through Responses.
- Do not copy the general Responses key into the FHL key field or infer that providers share credentials.
- Do not change SiliconFlow contracts, provider selection, repair prompts, or human review.
- Do not modify the external versioned FHL plugin cache.
- Do not guarantee recovery from the FHL provider's current HTTP 502 upstream failure.

## Decisions

### Use a project-owned direct Images API adapter for explicit GUI configuration

When `FhlRepairRenderer` receives a non-empty `api_key`, it will submit the image edit directly with the standard-library HTTP client. The multipart request contains the source image, prompt, `gpt-image-2`, one result, the tested `2048x1536` 4:3 size, PNG output, and base64 response format. This avoids depending on unsupported environment-variable behavior in an external plugin and makes the GUI values the actual request inputs.

Alternatives considered were modifying the shared plugin cache or rewriting its source into a temporary file. Both couple the application to plugin internals and can be overwritten by plugin updates. Passing a key on the command line was rejected because process listings can expose it.

### Keep the plugin only as a legacy fallback

When no GUI FHL key is configured, the existing Node plugin path remains available so older local installations that intentionally use the plugin worker pool do not break. A non-empty GUI key always selects the direct adapter and cannot be overridden by plugin state. The compatibility path never receives or borrows `responses_key` or `siliconflow_key`.

### Normalize only supported FHL edit endpoints

The configured URL may be the exact `/v1/images/edits` endpoint, an API root ending in `/v1`, or a host root. The renderer normalizes those forms to the edit endpoint and rejects missing host, unsupported scheme, query, fragment, or unrelated endpoint paths before network access. It does not reinterpret a Responses endpoint as an Images endpoint.

### Bound retries and diagnostics

HTTP 429, 502, 503, 504, and 524 are retried up to the existing four total attempts. Authentication, validation, and other non-retryable failures return immediately. Error bodies and exceptions are flattened, truncated, and scrubbed of the configured FHL key before entering the manifest or GUI. Tests inject the opener and sleep function so no external request or delay is required.

### Preserve provider-specific settings and exports

No settings migration or field rename is required. Existing `responses_*`, `fhl_*`, and `siliconflow_*` fields remain separate. GUI help text will explicitly state that all three credential pairs are independent, and redacted settings exports continue clearing image-provider keys.

## Risks / Trade-offs

- [Direct adapter drifts from the external plugin] -> Cover multipart fields, endpoint normalization, response parsing, retries, and size/model constants with focused contract tests; keep the plugin fallback intact.
- [Custom URL is malformed or is a Responses endpoint] -> Reject it before reading the source image or opening a network connection.
- [A provider error echoes the credential] -> Apply key replacement before persistence and cap diagnostics to a fixed length.
- [FHL returns a URL instead of base64] -> Fail explicitly without fabricating an output; the public Images contract used by this project requires base64.
- [Legacy plugin fallback still uses global configuration] -> Use it only when the GUI FHL key is empty and label the behavior as compatibility, never as the authoritative configured route.

## Migration Plan

No configuration migration is required. After deployment, users with a non-empty GUI FHL key automatically use the GUI-bound direct route. Users without one retain the previous plugin worker behavior. Rollback restores the Node-only renderer; settings and render manifests remain readable.

## Open Questions

None. A live successful image still depends on FHL upstream availability; HTTP 502 is reported as an external provider failure rather than a configuration failure.

## Verification

- Focused FHL routing, renderer, GUI dispatch, and frozen packaging regression suite: 20 passed.
- Broader related settings, workflow, SiliconFlow, GUI, construction-review, and packaging suite: 103 passed, with two unrelated pre-existing responsive-layout height assertions deselected after they independently reproduced as 38px versus legacy 46/48px expectations.
- Python compilation, Node syntax checking, secret-pattern scanning of changed artifacts, and strict OpenSpec validation passed.
- A runtime probe loaded the real GUI settings, verified the request URL and bearer value matched the dedicated `fhl_url`/`fhl_key`, produced a multipart Images edit request without starting the Node plugin, and confirmed the plugin config SHA-256 did not change.
- A non-generating authenticated request made with the GUI FHL key reached the configured edit endpoint and returned HTTP 400 `image file is required`, confirming current route/auth acceptance without incurring an image task. The earlier full-image diagnostic still ended at the separate FHL upstream HTTP 502 boundary; this change does not claim that external outage is repaired.
