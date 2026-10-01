## ADDED Requirements

### Requirement: Continuous Chinese queries SHALL produce searchable terms
The retrieval stage SHALL generate bounded searchable terms from continuous Chinese natural-language queries without requiring users to insert spaces, while preserving explicit English, numeric, and mixed-language terms.

#### Scenario: Natural Chinese question
- **WHEN** a user queries `结构裂缝如何修复`
- **THEN** retrieval SHALL issue one or more bounded lexical searches using terms such as `结构`, `裂缝`, and `修复`, and SHALL not treat only the entire sentence as the searchable token.

#### Scenario: Mixed Chinese and English query
- **WHEN** a user queries a string containing Chinese, ASCII words, and punctuation
- **THEN** retrieval SHALL preserve normalized ASCII terms and Chinese searchable terms without raising an FTS syntax error.

### Requirement: Standard and clause identifiers SHALL support exact recall
The retrieval stage SHALL normalize and exact-match standard identifiers and clause identifiers against the v2 structured fields when those identifiers are present in a query.

#### Scenario: Standard identifier variants
- **WHEN** a user queries `GB55021-2021` or `GB 55021-2021`
- **THEN** retrieval SHALL search the normalized `standard_number` field and rank exact standard matches above generic `GB` matches.

#### Scenario: Clause identifier variants
- **WHEN** a user queries `5.3.2` or `第5.3.2条`
- **THEN** retrieval SHALL search the normalized `clause_number` field and SHALL not discard the numeric identifier as an ordinary stop token.

### Requirement: Retrieval channels SHALL be fused without violating scope or hierarchy
The retrieval stage SHALL merge exact metadata, FTS5/BM25, and LIKE/character fallback candidates by `chunk_id`, preserve document scope and retrieval-role filtering, and hydrate context-only parents only after child ranking.

#### Scenario: Scoped multi-channel recall
- **WHEN** a query matches candidates through more than one channel and `document_ids` is provided
- **THEN** only retrieval-role children from the requested documents SHALL compete, duplicate chunk IDs SHALL be merged, and channel diagnostics SHALL be retained.

#### Scenario: No lexical match
- **WHEN** no channel produces a candidate and scoped fallback is enabled
- **THEN** retrieval SHALL return the existing `scoped_fallback` mode with `relevance_status=unknown` rather than labeling fallback chunks as relevant.
