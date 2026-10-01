## Context

`chunk._canonical_tables()` currently sorts Docling first and merges any same-page table whose fully normalized text is equal **or whose first 120 characters are equal**. Long engineering headers make the prefix heuristic unsafe: page 34 of the current 200-page concrete specification contains several tables with the same strength-grade header but different indicator rows (`fc`, `ft`, `fi`, `Ec`), and one valid table is lost.

The primary retriever already returns `RetrievalResult` with version, stage status, mode, relevance status, scope availability, requested scope IDs, anchors, context groups, and chunks. The standalone semantic CLI instead emits only `query` and lightweight `candidates`. `RerankResult` and `GenerationContext` preserve mode and relevance but omit the scope fields, so a scoped result cannot be audited after downstream stages.

Constraints include compatibility with existing CLI paths and candidate consumers, preservation of `pending_engineer_review`, no mutation of the active production database, and deterministic offline tests. The current real sample must retain all 61 converted table IDs after chunking and indexing.

## Goals / Non-Goals

**Goals:**

- Prevent false table merges caused by shared headers or fixed prefixes.
- Continue merging exact duplicate tables and conservatively merge equivalent detections produced by different extraction backends.
- Add an auditable semantic retrieval envelope without removing the existing lightweight `candidates` list.
- Preserve document scope, mode, relevance, and warnings through rerank and generation context.
- Verify focused behavior and the current real PDF-derived artifacts.

**Non-Goals:**

- Rebuild or activate the production knowledge base.
- Change the embedding model, vector format, ranking formula, prompt policy, or engineering review boundary.
- Treat expanded context as direct retrieval evidence.
- Introduce fuzzy table merging across ambiguous or conflicting detections.

## Decisions

### Use conservative table identity rather than prefix similarity

Each table is normalized into a rectangular cell matrix. Automatic duplicate detection uses:

1. exact normalized matrix equality on the same page with strongly overlapping bounding boxes when both boxes are available; or
2. cross-backend equivalence only when both tables have the same dimensions, strongly overlapping bounding boxes, and very high full-cell similarity.

Same-backend tables require exact full-matrix equality and, when both regions are known, strongly overlapping bounding boxes. A shared header, a shared prefix, a similar first row, or identical content in separate page regions is never sufficient. Exact/equivalent duplicates retain the preferred canonical record and append the other source to `alternative_sources`. Conflicting content remains as a separate table.

This is chosen over a relaxed text threshold because false negatives merely retain an extra reviewable table, while false positives silently destroy engineering evidence.

### Reuse the retrieval metadata vocabulary while preserving semantic candidates

Standalone semantic retrieval receives a dedicated additive envelope, `knowledge-semantic-retrieval.v1`, because its lightweight entries are score references rather than hydrated `ChunkRecord` objects. It uses the same field meanings as primary retrieval:

- `status` with `stage_version=semantic-retrieve.v1`;
- `retrieval_mode=semantic`;
- `relevance_status=hit|no_hit|unavailable`;
- `scope_available` and `scope_document_ids`;
- `warnings` inside `status`;
- existing `query` and `candidates` unchanged.

If standalone semantic execution fails, the CLI writes the same envelope with `status=failed`, a stable exception-class error code, `relevance_status=unavailable`, and no candidates before returning a non-zero exit code. This prevents stale successful JSON from being mistaken for the latest run.

This avoids misrepresenting lightweight candidates as full chunks while giving all retrieval stages the same audit vocabulary.

### Propagate scope fields additively downstream

`RerankResult` and `GenerationContext` gain `scope_available` and `scope_document_ids`. Their builders copy these fields from the upstream payload and preserve upstream warnings. Existing JSON consumers remain compatible because fields are additive and existing names remain unchanged.

### Establish failures before implementation

Focused tests first reproduce the long-shared-header false merge, verify exact cross-backend merging/provenance, require the semantic envelope for hit and no-hit cases, and require downstream scope propagation. The implementation follows only after those tests fail against current behavior.

## Risks / Trade-offs

- **Risk: Equivalent cross-backend tables with small OCR differences may no longer merge** → Prefer preserving both reviewable records; only use a very high full-matrix similarity with matching geometry and dimensions.
- **Risk: Bounding boxes can be absent or use slightly different coordinates** → Exact normalized matrices still merge; fuzzy equivalence is disabled without reliable geometry.
- **Risk: New semantic fields break strict external schemas** → Keep the existing `query` and `candidates` fields and make all new fields additive under a new schema version.
- **Risk: Scope metadata claims filtering that was not applied** → Populate requested IDs directly from CLI arguments and calculate `scope_available` only from candidates resolved within that requested scope.
- **Risk: Real-sample reruns overwrite prior diagnostics** → Write only to the existing test results tree and do not touch active runtime manifests or production databases.

## Migration Plan

1. Add failing focused tests and record the pre-fix failures.
2. Implement conservative table deduplication and additive contract fields.
3. Run focused and broader pipeline tests.
4. Re-run chunk, index, embedding consistency, semantic retrieval, scoped retrieve, rerank, and generation test launchers against the current test artifacts.
5. Confirm 61 unique table IDs, SQLite integrity, and semantic fingerprint consistency.

Rollback consists of reverting the affected source/test files and retaining the prior test artifacts. No database activation or irreversible migration is performed.

## Open Questions

None for this change. Any future unification that hydrates standalone semantic candidates into full `ChunkRecord` objects should be proposed separately because it changes performance and payload size.
