## 1. Runtime compatibility

- [ ] 1.1 Add explicit-manifest support to active RAG status and semantic runtime state inspection.
- [ ] 1.2 Fall back from an incompatible frozen user overlay to a healthy bundled baseline only when all selected IDs are available.
- [ ] 1.3 Preserve fail-closed behavior and add regression tests for compatible, incompatible, and incomplete scopes.

## 2. Release verification

- [ ] 2.1 Run focused tests, compile checks, and strict OpenSpec validation without mutating production RAG.
- [ ] 2.2 Build a new versioned portable candidate and validate isolated startup, scoped retrieval, and report knowledge retrieval.
- [ ] 2.3 Create and verify a new archive while preserving all prior releases.
