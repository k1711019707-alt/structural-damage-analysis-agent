## ADDED Requirements

### Requirement: GUI retrieval SHALL use bounded profile-scoped v2 context
The GUI SHALL construct bounded queries per generation profile, retrieve only the selected document/folder scope, and preserve v2 lexical, semantic, hierarchical, and provenance metadata in generation context.

#### Scenario: Damage report retrieval
- **WHEN** the report workflow has structured findings and a configured profile scope
- **THEN** retrieval SHALL use a bounded damage/report query, return scoped evidence, and preserve anchors, context groups, source markers, and review flags.

#### Scenario: Construction plan retrieval
- **WHEN** the construction-plan workflow has a validated report and deterministic repair plan
- **THEN** retrieval SHALL use a bounded repair/safety query and SHALL not send the entire evidence JSON as the lexical query.

### Requirement: GUI SHALL degrade without losing evidence boundaries
The GUI SHALL continue with lexical v2 or legacy retrieval when semantic retrieval is unavailable and SHALL continue generation from structured evidence when no knowledge-base hit exists.

#### Scenario: Semantic sidecar unavailable
- **WHEN** the sidecar is missing, stale, or its model cannot load
- **THEN** the workflow SHALL use deterministic retrieval and record a non-fatal warning.

#### Scenario: No relevant hit
- **WHEN** no relevant chunk is found within the selected scope
- **THEN** the workflow SHALL mark knowledge-base status accordingly and SHALL not treat scoped fallback chunks as relevant hits.
