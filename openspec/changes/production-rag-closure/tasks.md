## 1. Production activation

- [x] 1.1 Add active RAG manifest/path helpers with atomic write and rollback metadata.
- [x] 1.2 Add v2 database health inspection and legacy fallback selection.
- [x] 1.3 Add a rebuild/activate CLI that preserves legacy DB and source files.

## 2. Runtime and GUI integration

- [x] 2.1 Load active v2 database and optional compatible semantic sidecar in the runtime facade.
- [x] 2.2 Preserve anchors/context groups and retrieval diagnostics through GUI generation context.
- [x] 2.3 Add bounded profile-specific report and construction-plan query builders.
- [x] 2.4 Ensure no-hit and semantic-unavailable states remain non-fatal and auditable.

## 3. Validation

- [x] 3.1 Add read-only full rebuild and representative retrieval validation script.
- [x] 3.2 Add tests for activation, rollback, stale sidecar, scope, hierarchy, query bounds, and artifact protection.
- [x] 3.3 Rebuild a versioned production candidate and record source/hash/count manifest.
- [x] 3.4 Run focused tests, full tests, packaged/source smoke checks, and strict OpenSpec validation.
- [x] 3.5 Update RAG runbook with activation, rollback, rebuild, and verification commands.
