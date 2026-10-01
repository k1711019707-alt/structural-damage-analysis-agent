## 1. Contract Tests

- [x] 1.1 Add failing tests proving an explicit GUI FHL URL/key drives the actual multipart edit request and does not invoke the Node plugin.
- [x] 1.2 Add failing tests for provider credential isolation, URL normalization, missing/invalid configuration, base64 result persistence, bounded retries, and key redaction.
- [x] 1.3 Add regression coverage for the legacy plugin fallback and source/frozen routing behavior.

## 2. Runtime Implementation

- [x] 2.1 Implement the project-owned FHL Images API edit adapter with injectable transport, tested 4:3 parameters, bounded response sizes, and atomic output persistence.
- [x] 2.2 Route non-empty GUI FHL credentials through the direct adapter while preserving plugin fallback only for an empty GUI FHL key.
- [x] 2.3 Remove frozen-runtime behavior that disables the GUI-bound route and prevent the packaged launcher from persisting explicit GUI keys into plugin-global configuration.
- [x] 2.4 Clarify in both settings layouts that Responses, FHL, and SiliconFlow credentials are independent.

## 3. Verification

- [x] 3.1 Run focused FHL renderer, settings, GUI dispatch, and packaging tests with the project Qt interpreter.
- [x] 3.2 Run Python/Node syntax checks, the broader directly related test suite, and strict OpenSpec validation.
- [x] 3.3 Perform a secret-safe runtime probe showing the GUI-bound path uses the configured endpoint/key without consulting or mutating the plugin worker config; record upstream 5xx separately from local integration success.
