## ADDED Requirements

### Requirement: shared hybrid retrieval corpus
The index SHALL define one deterministic retrieval corpus consisting only of
non-empty `retrieval_role='retrieval'` chunks, and SHALL expose its fingerprint
and count to the vector sidecar.

#### Scenario: parent and child are indexed
- **WHEN** a document contains context-only parents and retrieval children
- **THEN** the manifest fingerprint/count include only retrieval children and
  FTS contains no parent rows.

### Requirement: stable text identity
The index SHALL preserve the same `text_search` and `content_hash` used by
both lexical and vector stages.

#### Scenario: vector sidecar checks a rebuilt index
- **WHEN** chunk text changes and the document is reindexed
- **THEN** the retrieval corpus fingerprint changes and stale vector entries
  can be detected without changing the public build API.

### Requirement: BM25 and vector fusion
The retrieval stage SHALL independently obtain scoped BM25 and vector
candidates, merge them by chunk id, and rank them with configurable weighted
reciprocal-rank fusion.

#### Scenario: both channels return candidates
- **WHEN** BM25 and vector retrieval both return scoped retrieval children
- **THEN** results expose both ranks and raw scores, include both weighted
  contributions, and report a hybrid retrieval mode.

#### Scenario: vector retrieval is unavailable
- **WHEN** the vector sidecar, model, or dependency cannot be used
- **THEN** BM25 and deterministic lexical channels still return results and
  the response contains a non-fatal fallback warning.
