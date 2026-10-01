## 1. Immutable portable resource staging

- [x] 1.1 Add a packaging helper that validates explicit FHL, Node, semantic-model, and active-RAG inputs and builds an isolated staging tree.
- [x] 1.2 Sanitize the staged active manifest, copy only selected runtime RAG artifacts and active source documents, and enforce path, WAL, source/destination digest, collision, and secret/path-redaction gates.
- [x] 1.3 Add temporary-fixture tests for healthy staging and fail-closed missing, escaping, changing, or rebuilding RAG inputs.

## 2. Frozen semantic and module runtime

- [x] 2.1 Resolve the packaged semantic model in frozen mode and require local-only sentence-transformer loading.
- [x] 2.2 Update the PyInstaller specification to consume only the staging tree and collect current knowledge-pipeline, semantic, RAG, and assistant dependencies.
- [x] 2.3 Layer frozen user RAG activation above the read-only bundled baseline and keep source-mode project activation unchanged.
- [x] 2.4 Add focused tests for packaged semantic-model preference, offline flags, bundled/user RAG precedence, writable activation, and current spec resource/module contracts.

## 3. Portable build and release manifest

- [x] 3.1 Update `build_portable.ps1` with explicit portable resource parameters, pre-PyInstaller staging, and cleanup-safe environment propagation.
- [x] 3.2 Extend `release_manifest.json` with portable model, RAG, semantic-model, and FHL identities while rejecting credentials and machine-specific source paths.
- [x] 3.3 Add a frozen candidate verification helper for file hashes, bundled RAG health, scoped retrieval, offline semantic initialization, and unrelated-working-directory startup.
- [x] 3.4 Add a versioned source-release builder/verifier that consumes the same immutable stage and includes offline RAG, semantic-model, FHL/Node, formal-model, and dependency-lock resources without user configuration.
- [x] 3.5 Make source-mode runtime discovery prefer release-bundled semantic and FHL/Node resources while preserving development fallbacks.

## 4. Verification and deferred release build

- [x] 4.1 Run focused packaging/runtime tests, the full source suite, compile checks, and strict OpenSpec validation without accessing the live production knowledge directory.
- [x] 4.2 After the user confirms the background multi-document RAG rebuild is complete, stage the newly active snapshot and build a versioned portable candidate without overwriting prior releases.
- [x] 4.3 Validate the complete extracted onedir candidate, bundled Node JavaScript syntax, release manifest/hash inventory, bundled RAG/semantic behavior, and EXE liveness; then create and test the archive.
- [x] 4.4 Validate and archive the extracted source candidate, then verify ZIP integrity and hashes for both deliverables without overwriting prior releases.
