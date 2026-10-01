## Why

The active production RAG database is queryable, but the audit found concrete quality and lifecycle gaps: NumPy-shaped RapidOCR results can fail on truth-value evaluation and leave pages unparsed; document/page quality diagnostics are not fully propagated into the index; duplicate source hashes are indexed as separate documents; active health checks accept stale schemas and do not enforce the manifest database hash; and GUI profile scope is resolved from the legacy catalog while retrieval uses a separately rebuilt v2 corpus. These gaps can make an apparently healthy database incomplete or make a GUI-selected document unavailable in v2.

## What Changes

- Normalize RapidOCR outputs without boolean evaluation of NumPy arrays and classify textless OCR failures as failed pages.
- Deduplicate page-quality inventories and propagate document identity, source metadata, quality score, page diagnostics, and conversion warnings through chunking and indexing.
- Deduplicate production inputs by source SHA-256 while preserving filename/path aliases in the build manifest.
- Strengthen activation health checks with manifest hash verification, minimum schema/column checks, source metadata and quality coverage, FTS parity, and optional semantic-sidecar validation.
- Bind the complete SQLite state (including WAL content) and semantic sidecar artifacts to activation evidence, and eliminate validate-one/load-another runtime races.
- Reject semantic manifests and vector payloads that exceed bounded resource limits or contain unsupported/non-finite numeric data.
- Resolve semantic sidecars through the portable user-data path helper instead of the process working directory.
- Make v2 scope misses explicitly fall back to the legacy selected scope, recording the fallback reason and retrieval backend.
- Expand production validation from structural counts to expected-query, expected-document, duplicate-source, quality-coverage, schema, and scope-availability checks.
- Guarantee that an exact source-title or standard-identifier query can seed retrieval from the matching indexed document instead of relying only on chunk-body token overlap.
- Use the currently configured GUI Responses API to adjudicate deterministic candidate blank pages with a strict JSON contract; never introduce a local vision model or silently exempt pages when the remote API is unavailable.
- Build and validate a new versioned candidate without overwriting the current active database; activate only after all required gates pass and rollback evidence is captured.

## Capabilities

### New Capabilities

- `rag-ingestion-quality-gates`: OCR-safe conversion, page completeness classification, diagnostic propagation, and content-hash deduplication.
- `rag-catalog-synchronization`: Explicit interoperability and fallback between the GUI legacy catalog and active v2 corpus.

### Modified Capabilities

- `production-rag-activation`: Enforce manifest integrity, current schema compatibility, quality coverage, and semantic-sidecar compatibility before reporting an active database healthy.
- `rag-production-validation`: Require relevance-aware representative validation and duplicate/quality/schema reporting, not only non-zero table counts.
- `gui-v2-rag-retrieval`: Preserve selected scope and fall back to the corresponding legacy scope when selected documents are absent from v2.

## Impact

- Affects `knowledge_pipeline/pdf_convert.py`, `chunk.py`, `index.py`, semantic-sidecar path handling, `runtime/rag_production.py`, `runtime/knowledge_base.py`, production build/validation scripts, tests, and the RAG runbook.
- Does not delete or overwrite the active v2 database, legacy database, source documents, or generated reports.
- Does not make semantic retrieval mandatory and does not add a vector database.
- Keeps citations, `pending_engineer_review`, profile scope isolation, and non-fatal AI generation fallback.
