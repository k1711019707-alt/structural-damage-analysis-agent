## Why

The portable build currently depends on machine-specific Codex plugin and Node paths and does not explicitly bundle the project-local active RAG database and semantic sidecar. A release built from this configuration is neither reproducible on another build machine nor guaranteed to provide the production knowledge retrieval behavior after extraction.

## What Changes

- Resolve the FHL Image Gen script and Node executable from explicit build parameters or portable project-owned staging inputs instead of hard-coded machine paths.
- Validate and stage the exact active RAG manifest, SQLite database, semantic index, semantic manifest, active source documents, and activation/validation evidence into the portable application resource tree.
- Fail the portable build when the active RAG snapshot is missing, unhealthy, mutable through a non-empty WAL, outside the project knowledge root, or inconsistent with its recorded SHA-256 digests.
- Ensure the frozen application includes the current knowledge-pipeline, RAG, and read-only assistant modules required by the GUI.
- Extend the release manifest with portable model, RAG, semantic, FHL runtime, and file-integrity metadata without including credentials or machine-specific source paths.
- Add source-level packaging tests and validate the extracted frozen executable against the bundled RAG rather than treating source tests as release proof.
- Produce a separate versioned source release from the same immutable RAG/model/FHL staging tree, with locked dependencies and no user credentials or machine-specific settings.

## Capabilities

### New Capabilities

- `portable-rag-release`: Builds a machine-independent Windows onedir release containing a strictly validated, read-only production RAG snapshot and its runtime dependencies.

### Modified Capabilities

None.

## Impact

- Affected build files: `packaging/damage_workflow_desktop.spec`, `packaging/build_portable.ps1`, FHL staging/runtime-hook support, and packaging tests.
- Affected runtime contracts: frozen resource discovery and project-local active RAG health checks; writable user imports remain under `%LOCALAPPDATA%`.
- Affected release artifacts: the onedir bundle and source release gain `knowledge_base/active_rag.json`, the selected versioned RAG directory, the offline semantic-model snapshot, and integrity metadata.
- Build prerequisites become explicit inputs. No API credentials, inactive knowledge-source documents, historical RAG candidates, or user settings are copied by this change. Source documents bound to the final active manifest are included by filename and SHA-256 so citations remain inspectable after deployment.
