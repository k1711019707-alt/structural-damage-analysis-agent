## Design

Define the retrieval corpus as rows where `retrieval_role='retrieval'` and
`text_search` is non-empty. Compute a deterministic SHA-256 fingerprint over
`(chunk_id, content_hash)` in stable source order. Store it in the
document row and manifest alongside `retrieval_chunk_count` and the FTS field
contract. The embedding sidecar can compare this fingerprint to its own source
fingerprint before semantic search; stale vectors remain filtered by existing
content-hash checks. Semantic retrieval compares the sidecar's global source
fingerprint with the live SQLite retrieval corpus before encoding the query;
on mismatch it falls back through the normal non-fatal semantic warning path.

Add `(document_id,retrieval_role,primary_page,location,part,chunk_id)` ordering
supporting the sidecar build and deterministic diagnostics. Do not insert
parents into FTS.

Retrieval builds independent BM25 and vector top-N candidate lists under the
same document scope. It merges by chunk id and computes weighted RRF using a
configurable constant. Exact clause/standard and LIKE channels remain useful
lexical supplements. Each result records raw BM25/cosine scores, per-channel
ranks, weighted contributions, and the final fusion score. Hybrid mode is
reported only when both BM25 and vector return candidates; otherwise the mode
states the actual active channel and records semantic fallback warnings.
