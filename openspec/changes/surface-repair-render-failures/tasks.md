## 1. Failure Diagnostics

- [x] 1.1 Add failing tests for HTTP-error extraction, generic return-code fallback and API-key redaction.
- [x] 1.2 Implement bounded, redacted FHL child-process failure summaries in render results and manifests.

## 2. GUI Outcome Reporting

- [x] 2.1 Add failing GUI tests for all-failed, partially-failed and all-usable render batches.
- [x] 2.2 Aggregate result statuses into truthful completed, partial and failed terminal states.
- [x] 2.3 Log the resolved output directory and each failed source filename with its safe reason.

## 3. Verification

- [x] 3.1 Run focused renderer and workflow tests with the project Qt interpreter.
- [x] 3.2 Run Python compilation, the complete test suite and strict OpenSpec validation.
- [x] 3.3 Verify the real incident manifest remains 5/5 failed and document the live FHL HTTP 502 boundary without claiming image output.
