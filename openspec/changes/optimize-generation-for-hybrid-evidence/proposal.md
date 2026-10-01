## Why

Hybrid retrieval now returns BM25/vector diagnostics, anchors, hydrated
parents, expanded context, table rows, image OCR, visual summaries, and
non-fatal fallback warnings. The generation stages mostly flatten these chunks
into text, so duplicate parent/child evidence consumes prompt budget and the
model cannot reliably distinguish direct hits, contextual expansion, OCR, or
model-generated visual descriptions.

## What Changes

- Extend the pipeline generation contract with retrieval mode, relevance,
  warnings, anchors, context groups, and normalized evidence groups.
- Build bounded evidence groups that keep anchors first, attach parent/context
  once, and suppress exact or contained parent-child repetition.
- Render generation prompts with evidence role, retrieval channels, evidence
  type, review state, and source markers without exposing raw ranking numbers as
  instructions.
- Add type-specific rules for tables, OCR, and model-generated visual evidence.
- Propagate retrieval warnings through the runtime facade and generation audit.

## Non-goals

- Do not change retrieval ranking, call an external model, or replace report
  and construction-plan JSON schemas.
- Do not remove provenance or engineer-review boundaries.

## Impact

Changes affect `knowledge_pipeline/contracts.py`, `generate.py`,
`runtime/knowledge_base.py`, `runtime/generation_context.py`, and focused tests.
All new fields have defaults for compatibility.
