## Why

The chunk stage currently treats same-page tables with the same first 120 normalized characters as duplicates, which can silently merge distinct engineering tables that share a long header but contain different indicator rows. In parallel, standalone semantic retrieval and downstream rerank/generation outputs do not expose the same status, mode, relevance, and document-scope fields as the primary retrieval contract, making end-to-end scope auditing unreliable.

## What Changes

- Replace fixed-prefix table deduplication with conservative full-content and cross-backend structural equivalence checks.
- Preserve distinct same-page tables when their data rows, dimensions, or indicator labels differ, even if their headers are identical.
- Preserve provenance from genuinely equivalent cross-backend table detections through `alternative_sources` without silently merging conflicting content.
- Make standalone semantic retrieval emit a versioned result envelope with stage status, retrieval mode, relevance status, scope availability, requested document IDs, warnings, and compatible candidates.
- Propagate retrieval mode, relevance status, document scope, and upstream warnings through rerank and generation-context outputs.
- Add focused regressions and a real converted-PDF assertion that all 61 source table IDs survive chunking and indexing.

## Capabilities

### New Capabilities

- `table-dedup-retrieval-contracts`: Conservative table identity rules and a common auditable retrieval metadata contract across semantic retrieval, reranking, and generation context.

### Modified Capabilities

None. The repository has no archived baseline specifications; this change consolidates and tightens behavior previously described only by unarchived changes.

## Impact

- Affects `knowledge_pipeline/chunk.py`, `contracts.py`, `semantic_retrieve.py`, `rerank.py`, `generate.py`, focused tests, and module test launchers/results.
- Keeps existing chunk, retrieval candidate, citation, and `pending_engineer_review` behavior compatible; new envelope fields are additive.
- Does not alter or activate the production knowledge database, active manifest, source PDFs, embedding model, or vector dimensions.
