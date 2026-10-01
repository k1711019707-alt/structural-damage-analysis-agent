## Context

The active GUI already rebuilds and validates versioned v2 SQLite databases and calls `knowledge_pipeline.retrieve.retrieve()` with adaptive routing. The production builder does not create a semantic sidecar, `pipeline_search_with_scope()` returns retrieval output without invoking `rerank_payload()`, runtime generation duplicates part of `knowledge_pipeline.generate`, and `route_external_answer()` is used by evaluation rather than the report, plan, and assistant workflows. The current Responses endpoint is configurable and may be OpenAI-compatible without supporting Web Search, so external routing must be capability-driven and fail safely.

## Goals / Non-Goals

**Goals:**

- Make every GUI-produced active candidate a validated lexical-plus-semantic corpus.
- Mirror the evaluated adaptive-rerank behavior in production while keeping direct anchors separate from expanded context.
- Use one canonical evidence/source contract for all GUI consumers.
- Attempt verified Web Search on a KB miss when the configured Responses provider supports it; otherwise continue as explicitly labelled model prior.
- Surface the effective semantic, rerank, hierarchy, and answer-source state in diagnostics and persisted manifests.

**Non-Goals:**

- Do not replace the local embedding model with a remote embedding API or introduce new credentials.
- Do not let external sources enter the SQLite knowledge base or use `[KB:...]` markers.
- Do not bypass active-v2 document scope, production validation, strict response schemas, or engineer review.
- Do not claim an OpenAI-compatible endpoint supports Web Search until a request returns verifiable sources.

## Decisions

1. **Semantic sidecar is a required GUI candidate artifact.** After indexing, the builder creates `semantic.npz` and its companion manifest from the exact retrieval-child order in the candidate SQLite. Strict database health then validates shape, safe dtypes, corpus fingerprint, chunk/content identity, and SHA. GUI synchronization passes the sidecar to `activate_rag()`. A build/model/validation failure rejects the candidate and preserves the old active manifest. This is chosen over runtime lazy embedding because activation must remain reproducible and queries must not block on first-use model work.

2. **Reuse the existing local model contract.** Use `BAAI/bge-small-zh-v1.5`, `sentence-transformers==5.7.0`, normalized float32 embeddings, and configurable device/batch options. The model name, path, count, dimension, and source fingerprint stay in the semantic manifest. Existing cache/model resolution is used; the application never downloads or switches models silently during a retrieval request.

3. **Rerank a bounded direct-candidate pool.** Runtime retrieval requests at least `max(30, top_k * 5)` direct candidates with a bounded expanded character budget. It passes direct anchors to `rerank_payload()`, selects final `top_k`, then reattaches only context groups belonging to selected anchors. Expanded context cannot displace direct evidence. Final character limits are enforced after assembly. This mirrors the evaluation configuration while preserving provenance and latency bounds.

4. **Return explicit rerank diagnostics.** `KnowledgeBaseSearchResult` and `GenerationContext` carry `reranker`, candidate count, selected count, rerank status, route diagnostics, and preserved retrieval warnings. Compatibility defaults keep existing callers functional.

5. **Canonical generation assembly delegates to `knowledge_pipeline.generate`.** Runtime converts its typed chunks and diagnostics into the pipeline retrieval contract, invokes the pipeline context builder, and maps the result back to the GUI `GenerationContext`. Runtime-only settings snapshots and service prompts remain in the runtime dataclass, but evidence groups, source mode, external metadata, citations, and review warnings come from the canonical pipeline implementation.

6. **External routing occurs after retrieval sufficiency is known.** A non-empty scoped fallback is not automatically considered relevant KB evidence. A hit with direct anchors routes to `knowledge_base`; a miss invokes `route_external_answer()`. A Responses Web Search adapter is injected from the existing GUI URL/key/model only for generation workflows. It uses the provider's tool capability and accepts results only when URL and title are present; errors, unsupported tools, empty sources, or invalid responses route to `model_prior` with a redacted reason. The read-only assistant uses the same router and can receive the same optional provider.

7. **External source content remains bounded and separate.** Verified sources contain title, URL, accessed time, and a bounded excerpt. They are rendered in a distinct external-source section and persisted in manifests. They never receive KB markers or alter scope IDs.

8. **GUI status reports effective behavior, not configured intent.** Knowledge-base status includes active semantic health/model/count, rerank stage/version, and last answer-source mode. If semantic or rerank is unavailable in an existing older active corpus, retrieval remains explicit about the missing stage; new GUI synchronization, however, cannot activate a lexical-only candidate.

## Risks / Trade-offs

- [Embedding rebuild increases synchronization time and memory] -> Run it in the existing background sync worker, use bounded batches/device selection, and keep the old active corpus until atomic activation succeeds.
- [The embedding model is absent or cannot download] -> Fail the new candidate with an actionable GUI message; never silently activate a lexical-only replacement.
- [Candidate-pool reranking increases P95 latency] -> Bound the pool and expanded context, retain deterministic scoring, and record timing/candidate diagnostics.
- [Configured Responses provider does not support Web Search] -> Treat tool errors as capability absence and route to labelled model prior without blocking report/plan generation.
- [External results contain unverifiable or malicious text] -> Require HTTP(S) URL plus title, bound excerpts, label as external untrusted evidence, and retain strict output schema and engineer review.
- [Canonical generation mapping changes prompts] -> Add contract tests comparing anchors, groups, warnings, citations, hashes, and source mode across report, plan, and assistant paths.

## Migration Plan

1. Add contracts and tests for semantic candidate building, rerank assembly, canonical generation mapping, and external routing.
2. Update the production builder and GUI synchronization to create and activate semantic sidecars.
3. Update runtime retrieval and all GUI consumers to use reranking and unified source routing.
4. Rebuild a fresh versioned candidate from the current GUI catalog, strictly validate it, and atomically activate it; preserve the current active manifest as rollback state.
5. Run focused RAG/GUI tests, a live local retrieval smoke that confirms semantic and rerank diagnostics, strict OpenSpec validation, and GUI startup/status verification. Do not invoke paid external generation during automated verification.

## Open Questions

- The configured OpenAI-compatible endpoint's Web Search support cannot be established from local configuration alone. Production will use runtime capability detection and verified source extraction; model-prior fallback remains the deterministic outcome when support is absent.
