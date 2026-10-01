## ADDED Requirements

### Requirement: Explicit answer source mode
The generation pipeline MUST label every answer as `knowledge_base`, `web_search`, or `model_prior` and MUST preserve this label through the runtime generation context.

#### Scenario: Knowledge-base evidence is sufficient
- **WHEN** scoped retrieval returns sufficient supported evidence
- **THEN** the answer source mode is `knowledge_base` and knowledge-base citations remain available.

#### Scenario: Knowledge base has no answer
- **WHEN** scoped retrieval determines that the current knowledge base has no sufficient evidence
- **THEN** the pipeline selects `web_search` when a verified search capability is available, otherwise it selects `model_prior`.

### Requirement: Verifiable web-search evidence
The pipeline MUST mark a fallback as `web_search` only when the provider returns verifiable external sources and MUST keep those sources distinct from knowledge-base citations.

#### Scenario: Web search returns sources
- **WHEN** an enabled provider returns external results with source title, URL, and access metadata
- **THEN** the answer may use `web_search` mode and exposes those sources without formatting them as `[KB:...]`.

#### Scenario: Web search is unavailable or unverified
- **WHEN** the provider lacks search capability, the call fails, or no verifiable source is returned
- **THEN** the pipeline falls back to `model_prior` rather than claiming successful web search.

### Requirement: Model-prior safety boundary
Model-prior answers MUST state that they are not supported by the current knowledge base, may be outdated, require verification, and retain engineering review requirements for high-risk content.

#### Scenario: Answer from model knowledge
- **WHEN** `model_prior` mode is selected
- **THEN** generation instructions and returned diagnostics contain the non-KB, freshness, verification, and engineer-review warnings.
