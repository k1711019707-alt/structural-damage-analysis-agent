## 1. Runtime path contract

- [x] 1.1 Add project-local active manifest discovery with user-data fallback.
- [x] 1.2 Make manifest-relative database and sidecar resolution use the selected manifest directory with traversal protection.
- [x] 1.3 Update activation, rollback, and health diagnostics to use the selected target and report storage scope.

## 2. Migration and project assets

- [x] 2.1 Copy the current active v2 directory into the versioned project `knowledge_base` directory and verify required file hashes.
- [x] 2.2 Write the project-local active manifest atomically with project-relative paths and preserved validation evidence.
- [x] 2.3 Update the production RAG runbook with project-local activation, backup, and rollback instructions.

## 3. Verification

- [x] 3.1 Add tests for project-manifest precedence, legacy fallback, relative path safety, and storage diagnostics.
- [x] 3.2 Run active status, scoped retrieval, production validation, and the focused/full test suites.
- [x] 3.3 Record migration source/destination hashes and retain the AppData version as an explicit rollback backup.
