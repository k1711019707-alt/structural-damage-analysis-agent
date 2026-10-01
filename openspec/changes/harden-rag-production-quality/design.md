## Context

The desktop maintains a legacy SQLite catalog for GUI folders/documents and a separately activated v2 RAG database. The active v2 build predates the current index schema, contains content-identical duplicate documents, and includes a document with 77 OCR failures caused by NumPy array truth-value evaluation. Current health checks only require three tables and non-zero counts, so incomplete or stale data is still reported healthy.

## Decisions

1. Normalize OCR result alternatives with explicit `is None`/length checks. Never use Python boolean `or` or `if value` on possible NumPy arrays.
2. Treat a routed OCR page with neither usable native text nor OCR text as a failed page and retain the exact warning. Keep page lists unique and sorted before quality scoring.
3. Add document/page quality metadata to the chunk payload. Index it into `pipeline_documents` and `pipeline_pages` so activation can evaluate the same facts produced by conversion.
4. Deduplicate build inputs by SHA-256. One canonical source becomes one indexed document; alternate paths/names remain auditable aliases in the build manifest.
5. Define a minimum compatible schema using required tables and required columns rather than only a generic v2 label. Health output shall distinguish structural health, integrity health, and quality warnings.
6. When validating an active manifest, compare the recorded database SHA-256 with the actual database. If a semantic path is configured, resolve it relative to the user knowledge root and validate its file/manifest/fingerprint before loading it.
7. Preserve GUI scope as authoritative. If the selected legacy document IDs have no rows in the active v2 corpus, query the same IDs in legacy and expose an explicit `legacy_scope_fallback` mode/warning. Never broaden to unselected v2 documents.
8. Keep the current active database untouched while building a new candidate. Activation remains a separate final operation after candidate validation.
9. Keep retrieval ordering changes out of this change except where needed for backend/fallback diagnostics. Dedicated reranking evaluation remains a follow-on after corpus quality is trustworthy.
10. Treat a SQLite database as a logical database, not only a main-file byte stream. Activation and health validation shall reject or checkpoint unsupported WAL state before hashing/snapshotting so committed WAL content cannot escape the recorded digest.
11. Bind semantic NPZ and companion-manifest digests into activation evidence. A request shall load the exact semantic path and digests returned by the same validated active-state read; it must not reread the active manifest between validation and retriever construction.
12. Runtime health caching shall not make a previously validated digest authoritative for a mutable database path. Before retrieval, verify the live digest (and semantic digests when configured), even when path, size, timestamps, and file identity appear unchanged.
13. Use one bounded semantic artifact loader for health and retrieval. Enforce companion-manifest size limits, supported real numeric vector dtypes, and finite vector values before an artifact can be reported healthy.
14. Treat exact normalized source-title and standard-identifier matches as deterministic document-level retrieval seeds. Preserve scope filtering, citations, hierarchical expansion, and existing ranking for non-title queries; this is not a general reranker change.
15. Blank-page adjudication is a bounded second-stage remote vision check. Only pages already classified as `blank_or_unreadable` by deterministic preflight are eligible. The adapter shall use the existing GUI Responses API settings, require strict JSON (`blank`, `non_blank`, or `uncertain`), and fail closed to the existing OCR/quality path on timeout, invalid output, missing credentials, or API errors. A high-confidence blank decision skips OCR/chunk generation but remains in page inventory and audit evidence.

## Safety and Rollback

- All production builds target a new versioned directory.
- Source documents and `knowledge_base.sqlite3` remain unchanged.
- Candidate validation is read-only with respect to the active manifest.
- Activation is allowed only after strict validation; rollback metadata is verified immediately afterward.
- Databases with live WAL state are never activated from a main-file-only digest or snapshot.
- Activated semantic artifacts are digest-bound and loaded from the already validated path.
- Remote blank-page decisions never remove page-inventory rows and never bypass the quality gate without recorded model output and deterministic preflight evidence.
- API keys and raw credentials are excluded from manifests and reports.

## Verification

- Unit tests with NumPy arrays for RapidOCR fields and failed-page classification.
- Contract tests for conversion -> chunk -> index quality propagation.
- Build tests for SHA deduplication and alias recording.
- Health tests for schema columns, database SHA mismatch, quality thresholds, FTS parity, and semantic relative paths.
- Runtime tests for selected-scope v2 success, selected-scope legacy fallback, and no scope broadening.
- Representative query validation with expected document IDs/standard identifiers.
- Focused and full project tests plus strict OpenSpec validation.
- Build a new candidate and compare document/page/chunk/duplicate/quality metrics before any activation.
