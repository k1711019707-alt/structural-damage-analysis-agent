## 1. Semantic production artifacts

- [x] 1.1 Extend the production builder to generate a source-bound semantic sidecar and include its metadata in build and validation outputs.
- [x] 1.2 Pass the semantic sidecar through GUI rebuild, strict activation, active status, and user-facing synchronization diagnostics.
- [x] 1.3 Add regression tests proving semantic build/activation success, identity validation, and preservation of the previous active RAG on failure.

## 2. Production reranking

- [x] 2.1 Add bounded candidate-pool retrieval and deterministic direct-anchor reranking to `pipeline_search_with_scope`.
- [x] 2.2 Reattach only selected-anchor hierarchical context, enforce final Top-K/character limits, and propagate rerank diagnostics.
- [x] 2.3 Add focused tests for ordering, scope isolation, expanded-context subordination, empty candidates, and compatibility callers.

## 3. Unified generation and external routing

- [x] 3.1 Delegate runtime evidence/source assembly to `knowledge_pipeline.generate` while preserving runtime settings snapshots and service prompts.
- [x] 3.2 Implement a capability-detected Responses Web Search provider with verified source extraction and redacted model-prior fallback.
- [x] 3.3 Connect source routing and canonical generation context to damage report, construction plan, and read-only assistant workflows.
- [x] 3.4 Persist and display effective semantic, rerank, hierarchy, answer-source, external-source, and freshness diagnostics without exposing credentials.

## 4. Verification and activation

- [x] 4.1 Run focused semantic, retrieval, rerank, generation, assistant, GUI, and production-quality tests with the project interpreter.
- [x] 4.2 Strictly validate the OpenSpec change and compile modified modules.
- [x] 4.3 Rebuild and atomically activate a fresh semantic production RAG from the current GUI catalog, preserving rollback state.
- [x] 4.4 Verify active health, semantic retrieval plus rerank diagnostics, GUI startup/status, and source-mode fallback contracts without invoking paid external generation.
