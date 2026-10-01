## Design

Normalize every retrieved item into an evidence descriptor containing role
(`anchor`, `parent_context`, `expanded_context`), evidence type, retrieval
channels, source, review flags, and safe text. Group by anchor/context relation;
when relation data is absent, preserve current order and infer parent links.

Prompt rendering includes concise labels but not raw BM25/cosine/RRF values.
Exact duplicates are emitted once. A parent whose normalized text contains the
entire child remains available only when it adds material context; otherwise it
is represented as a context reference. Tables keep headers and table ids.
Visual OCR is identified as extracted text; `model_generated` visual summaries
are explicitly non-authoritative and require review.

The runtime facade carries retrieval warnings into `KnowledgeBaseSearchResult`.
Runtime generation context stores warnings/evidence groups in its manifest and
prompt, while maintaining old constructor defaults and existing report/plan
service inputs.
