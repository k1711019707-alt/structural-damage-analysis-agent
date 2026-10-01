## ADDED Requirements

### Requirement: GUI consumers use one canonical evidence contract
Damage reports, construction plans, and the read-only assistant SHALL use the canonical pipeline generation assembly for evidence groups, anchors, context groups, warnings, citations, answer-source mode, external sources, and engineer-review requirements.

#### Scenario: Knowledge-base evidence is available
- **WHEN** reranked direct anchors are available inside the selected active-v2 scope
- **THEN** every GUI consumer receives `answer_source_mode=knowledge_base`, preserves KB source markers, and persists matching evidence diagnostics

### Requirement: Knowledge-base misses use explicit external routing
The GUI runtime SHALL pass a knowledge-base miss through the external-answer router and MUST distinguish verified Web Search from model-prior continuation.

#### Scenario: Verified Web Search succeeds
- **WHEN** the configured provider supports Web Search and returns at least one result containing an HTTP(S) URL and title
- **THEN** the answer source mode is `web_search`, external sources are preserved with access metadata, and no source is represented as a KB citation

#### Scenario: Web Search is unsupported or unverified
- **WHEN** the provider rejects the tool, fails, or returns no verifiable URL/title source
- **THEN** the answer source mode is `model_prior`, the failure reason is redacted and persisted, and the GUI warns that the content may be stale and requires engineering review

### Requirement: External routing preserves production boundaries
External routing MUST NOT broaden the selected knowledge-base scope, mutate the active database, persist credentials, or suppress strict response validation and human review.

#### Scenario: External fallback follows a scoped miss
- **WHEN** the selected scope has no sufficient evidence
- **THEN** scope IDs remain unchanged, external evidence is stored separately, and generated engineering conclusions remain pending engineer review

### Requirement: GUI reports the effective answer source
The current-file workflow and read-only assistant SHALL expose whether the effective source is knowledge base, verified Web Search, or model prior without displaying secrets or internal exception paths.

#### Scenario: Generation completes after fallback
- **WHEN** a report, plan, or assistant answer completes using an external route
- **THEN** the GUI status and persisted audit identify the effective source mode and any freshness warning
