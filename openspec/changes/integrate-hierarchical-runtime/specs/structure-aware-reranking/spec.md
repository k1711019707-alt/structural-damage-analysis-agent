## ADDED Requirements

### Requirement: Reranking SHALL use semantic and structural signals
The deterministic reranker SHALL combine semantic score, fusion score, anchor status, heading overlap, lexical overlap, and stable source order while keeping expanded context below direct anchors when relevance is comparable.

#### Scenario: Semantic anchor rerank
- **WHEN** candidates include semantic scores and direct anchors
- **THEN** the reranker SHALL rank a high-scoring direct anchor above a lower-scoring expanded context candidate and SHALL preserve original scores for audit.

#### Scenario: Legacy rerank
- **WHEN** candidates lack semantic or structural metadata
- **THEN** the reranker SHALL continue to produce deterministic output using existing lexical overlap and original order features.
