## Context

The repository contains a legacy `runtime/knowledge_base.py` database facade and a newer `knowledge_pipeline` v2 implementation. The desktop GUI must remain usable when v2 data or optional semantic dependencies are unavailable, while production activation must be atomic and reversible.

## Decisions

1. Keep the legacy database untouched and create a separate versioned v2 database under the writable application-data root.
2. Store an activation manifest beside the active database. The manifest records database path, schema, source-document fingerprints, chunk counts, semantic sidecar path/fingerprint, build time, and rollback database.
3. Build v2 data from managed source files using `pdf_convert -> chunk -> index`; reuse existing source copies and never delete originals.
4. Treat semantic retrieval as optional. A sidecar is used only when its database fingerprint and chunk content hashes match; otherwise lexical v2 retrieval remains active and records a warning.
5. Extend the runtime facade to prefer the active v2 database, use hierarchical expansion, and expose anchors/context groups. If activation is absent or unhealthy, use the legacy adapter.
6. Construct bounded profile-specific queries from project overview, damage classes, component fields, and repair objectives rather than serializing all evidence into one query.
7. Provide a validation script that can rebuild into a temporary directory, compare document coverage, run representative queries, and emit a machine-readable report without changing the active database.

## Safety and Rollback

- Build into a temporary/versioned path, verify before activation, and atomically replace only the manifest.
- Preserve the previous manifest and legacy database as rollback targets.
- Never persist API credentials in manifests or validation output.
- Keep `pending_engineer_review` and source markers through generation.

## Verification

- Unit tests for activation, stale sidecar rejection, v2/legacy selection, bounded query construction, and rollback.
- Full test suite with the project interpreter and offscreen Qt.
- Full knowledge-base rebuild report with source count/hash coverage and representative natural Chinese, standard-number, clause-number, scoped, and hierarchical queries.
- OpenSpec strict validation for this change.
