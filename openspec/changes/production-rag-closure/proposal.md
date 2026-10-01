## Why

The optimized v2 RAG modules now exist, but the desktop application still reads the legacy knowledge database and does not consistently enable v2 semantic and hierarchical retrieval. This change closes the production gap while preserving the legacy database as a recoverable fallback and making the active database, semantic sidecar, scope, provenance, and validation status explicit.

## What Changes

- Build a versioned production v2 knowledge database from the managed source documents without overwriting the legacy database.
- Add an atomic migration/activation manifest with source hashes, schema version, chunk counts, semantic-index fingerprint, and rollback target.
- Make the runtime knowledge-base facade select the active v2 database and optionally load a matching local semantic sidecar.
- Route GUI report and construction-plan retrieval through v2 lexical, semantic, hierarchical, and deterministic reranking stages while retaining legacy fallback.
- Add controlled profile-specific query construction instead of sending the entire evidence JSON as the retrieval query.
- Add production smoke/evaluation commands for document coverage, scope isolation, natural Chinese queries, standard/clause identifiers, parent hydration, semantic staleness, and fallback behavior.
- Update user-facing and developer documentation with activation, rollback, rebuild, and verification procedures.

## Capabilities

### New Capabilities

- `production-rag-activation`: Versioned v2 database/semantic-sidecar activation, manifest, rollback, and health checks.
- `gui-v2-rag-retrieval`: Profile-scoped GUI retrieval using v2 lexical, optional semantic, hierarchical, and provenance-preserving results.
- `rag-production-validation`: Repeatable full-knowledge-base rebuild and retrieval-quality smoke/evaluation reporting.

### Modified Capabilities

- `runtime-hierarchical-context`: Require the active production retrieval path to preserve anchors, expanded context, scope, and provenance.
- `local-semantic-retrieval`: Require production sidecar compatibility checks and safe lexical fallback when unavailable.

## Impact

- Affects `runtime/knowledge_base.py`, `runtime/generation_context.py`, `runtime/damage_workflow_gui.py`, settings models/store, and new migration/validation scripts.
- Adds a versioned production database and optional `.npz` semantic sidecar under the writable per-user application-data directory; the legacy database remains untouched.
- Adds no remote service requirement and keeps semantic retrieval optional/offline-capable.
- Requires the existing project Python environment and current local parsing/embedding dependencies; no new vector database is introduced.
