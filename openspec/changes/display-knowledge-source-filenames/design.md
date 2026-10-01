## Context

Retrieved chunks already carry `document_id`, `source_marker`, `location`, and metadata derived from active-v2 indexing. The index also stores the source filename in `pipeline_documents.source_name`. Generation manifests retain most chunk metadata, but the report and construction Markdown render model-returned citation strings directly. Human-review save paths reconstruct Markdown from the validated business model without a generation context, so a renderer that queries only the current database would make old artifacts mutable and non-reproducible.

The assistant has a working index-backed filename resolver, but generation artifacts need a persistence contract independent of assistant UI state.

## Goals / Non-Goals

**Goals:**

- Show the indexed source basename and page/location beside every resolved KB marker in report and construction-plan Markdown.
- Preserve the raw KB marker as the stable audit identity.
- Freeze citation display metadata at initial persistence and reuse it during review saves.
- Fail closed for missing or conflicting metadata without inventing provenance.

**Non-Goals:**

- Do not change model prompts, strict JSON schemas, retrieval/reranking, or active-v2 activation.
- Do not treat filenames as stable document identity or remove internal KB markers.
- Do not rewrite arbitrary prose that merely resembles a citation.
- Do not weaken report confirmation, construction review, `hold`, rendering, or release gates.

## Decisions

### Persist a deterministic citation catalog in each artifact envelope

Create a shared catalog keyed by exact `source_marker`. Each entry contains `document_id`, sanitized `source_name`, and normalized `location`. Build it only from retrieved chunk fields and authoritative metadata, de-duplicate identical markers, and reject conflicting names for one marker. Store the catalog outside the strict report/plan business model so remote-output schemas remain unchanged.

Querying the active database during every render was rejected because review saves could silently change historical citations. Adding citation fields to the remote draft schemas was rejected because the model must not author provenance.

### Render exact markers through one shared formatter

Replace exact `[KB:...]` markers in citation-bearing text with a compact readable form containing `来源：<basename>` plus the page/location and `内部标识：<marker>`. Use `PureWindowsPath` and `Path` basename handling so neither Windows nor POSIX source paths leak local directories.

If a marker has no unambiguous catalog entry, keep the marker visible and append `来源未解析`. This preserves audit evidence and makes the gap explicit.

### Apply citation rendering at Markdown boundaries only

Keep validated report and construction models unchanged. Apply deterministic rendering to report standards-basis text and all construction text/list fields while composing Markdown. Persist the same catalog in JSON and pass it back into the renderer after human review.

### Derive names from retrieval metadata, with authoritative index fallback at initial generation

Prefer the retrieved chunk metadata captured by the generation context. If a chunk lacks a name, resolve its document ID against the active-v2 `pipeline_documents` table once while the initial artifact is persisted. The resolved result is then frozen in the envelope. Model-authored names are never used as the source filename.

## Risks / Trade-offs

- [Older artifacts have no catalog] -> Preserve their raw markers as unresolved; do not mutate history by guessing from a later index.
- [Same marker has conflicting metadata] -> Exclude the conflicting catalog entry and render it unresolved.
- [A model repeats a marker in many fields] -> Render every occurrence from one de-duplicated catalog entry.
- [Filename changes after ingestion] -> Keep the immutable internal marker visible and use the filename only as a human-readable label.

## Migration Plan

1. Add failing tests for report, construction, review re-save, path sanitization, de-duplication, and unresolved markers.
2. Implement the shared catalog builder and renderer.
3. Persist/reuse the catalog in both artifact envelopes.
4. Run focused and full regression suites and strict OpenSpec validation.

No database or business-schema migration is required. Rollback removes the renderer/catalog integration; existing envelopes tolerate the additional top-level field.

## Open Questions

None.
