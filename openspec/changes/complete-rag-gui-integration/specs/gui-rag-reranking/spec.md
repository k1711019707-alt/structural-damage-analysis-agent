## ADDED Requirements

### Requirement: Production GUI retrieval executes deterministic reranking
Report, construction-plan, and assistant retrieval SHALL execute the structure-aware deterministic reranker after adaptive candidate retrieval and before final Top-K selection.

#### Scenario: Adaptive candidates are reranked
- **WHEN** active-v2 retrieval produces direct anchors
- **THEN** the runtime reranks a bounded direct-candidate pool using lexical, semantic, fusion, heading, anchor, and stable-order features and returns the configured final Top-K

#### Scenario: Reranking receives no candidates
- **WHEN** scoped retrieval produces no relevant direct anchors
- **THEN** the runtime records an explicit no-candidate rerank status and continues through external answer routing rather than fabricating a ranked hit

### Requirement: Hierarchical context remains subordinate to direct evidence
The runtime MUST rerank direct anchors separately and SHALL attach only bounded same-document context associated with selected anchors.

#### Scenario: Expanded context is present
- **WHEN** adaptive hierarchy routing expands one or more candidate anchors
- **THEN** expanded chunks cannot displace a direct anchor, cannot be reported as direct evidence, and are removed when their anchor is not selected

### Requirement: Rerank provenance is visible
The runtime result, generation manifest, and GUI diagnostics SHALL record the reranker stage/version, candidate count, selected count, retrieval mode, route diagnostics, and warnings.

#### Scenario: Report generation uses reranked evidence
- **WHEN** a report or plan is generated from RAG evidence
- **THEN** its persisted generation manifest identifies the effective reranker and the final selected direct anchors

