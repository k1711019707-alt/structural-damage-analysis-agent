## Why

Damage reports and construction plans currently expose internal markers such as `[KB:00474f7b00ed539d4702:page:31]` without the authoritative indexed filename. The filename already exists in active-v2 retrieval metadata, so omitting it makes otherwise valid citations difficult for engineers to understand and verify.

## What Changes

- Build a deterministic citation catalog from retrieved knowledge-base metadata at generation time.
- Persist that catalog in the report and construction-plan envelopes so human-review saves remain stable even if the active index later changes.
- Render every resolved knowledge-base marker in both Markdown documents with the source filename, page/location, and original internal marker.
- Preserve unresolved markers and label them as unresolved instead of inventing filenames or locations.
- Display only the source basename, never a full local filesystem path.

## Capabilities

### New Capabilities

- `readable-generation-citations`: Authoritative, persisted, human-readable knowledge-source citations for damage reports and construction plans.

### Modified Capabilities


## Impact

- Shared generation-context/citation utilities will create and render a deterministic source catalog.
- `runtime/responses_damage_report.py` and `runtime/responses_construction_plan.py` will persist and reuse the catalog across initial generation and human-review saves.
- Focused report, construction-plan, and provenance tests will cover filename resolution, path sanitization, unresolved markers, de-duplication, and review re-save behavior.
- No change to strict remote-output schemas, retrieval ranking, active-v2 scope, review gates, `hold`, `construction_released=false`, cancellation, timeout, or retry behavior.
