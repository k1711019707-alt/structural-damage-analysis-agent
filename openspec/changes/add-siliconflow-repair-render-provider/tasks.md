## 1. Provider Contracts

- [x] 1.1 Add failing tests for SiliconFlow request fields, data-URL image input and omitted `image_size`.
- [x] 1.2 Add failing tests for result download, manifest persistence, malformed responses, HTTP diagnostics and key redaction.
- [x] 1.3 Implement the SiliconFlow repair renderer with injectable HTTP transport and atomic local output.

## 2. Settings And Dispatch

- [x] 2.1 Add backward-compatible provider, URL, key and model fields to unified settings and redacted exports.
- [x] 2.2 Add the provider selector and separate SiliconFlow controls to both maintained settings layouts.
- [x] 2.3 Dispatch `RepairRenderWorker` to FHL or SiliconFlow without changing review, cancellation or result aggregation behavior.
- [x] 2.4 Add GUI/settings regression tests for defaults, persistence, redaction and provider dispatch.

## 3. Verification

- [x] 3.1 Run focused renderer, settings, construction-review and packaging tests with the Qt project interpreter.
- [x] 3.2 Run Python compilation, the directly related 59-test suite and strict OpenSpec validation; exclude unrelated Docling/PDF tests from this change boundary.
- [x] 3.3 Document that a real paid SiliconFlow render remains pending until a valid local SiliconFlow key is configured.
