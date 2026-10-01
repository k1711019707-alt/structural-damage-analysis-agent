## Why

The production GUI currently calls the adaptive retrieval stage but leaves the local semantic sidecar unbuilt, the deterministic reranker outside the runtime call chain, the standalone generation contract only partially reused, and the external-answer router confined to evaluation paths. As a result, the strongest evaluated RAG configuration and its source-bound fallback semantics are not the behavior users receive from report, construction-plan, and read-only assistant workflows.

## What Changes

- Build a local semantic sidecar for every GUI-produced production RAG candidate, validate it against the candidate SQLite corpus, and bind it into atomic activation and runtime health checks.
- Retrieve a bounded candidate pool in the GUI runtime, rerank direct anchors with the deterministic structure-aware reranker, and preserve subordinate hierarchical context without allowing expanded chunks to displace direct evidence.
- Make the standalone `knowledge_pipeline.generate` contract the canonical evidence/source assembly path used by runtime `GenerationContext`, including anchors, context groups, warnings, source mode, external sources, and review requirements.
- Route knowledge-base misses through `knowledge_pipeline.external_fallback`: use verified Web Search sources only when a provider actually returns URL/title evidence, otherwise label the continuation as model prior with freshness and engineering-review warnings.
- Apply the completed path consistently to damage reports, construction plans, and the embedded read-only assistant, and expose the effective semantic/rerank/source state in GUI-visible status and persisted generation diagnostics.
- Preserve strict active-v2 scope isolation, fail-closed candidate activation, bounded context budgets, local fallback behavior, and human review boundaries.

## Capabilities

### New Capabilities

- `production-semantic-rag`: GUI rebuilds produce, validate, activate, and report a source-bound local semantic sidecar.
- `gui-rag-reranking`: GUI retrieval reranks a bounded direct-candidate pool and reattaches only provenance-safe hierarchical context.
- `unified-rag-generation-routing`: report, plan, and assistant paths share canonical evidence assembly and explicit knowledge-base, verified-web, or model-prior source routing.

### Modified Capabilities

- None.

## Impact

- Affects `scripts/build_production_rag.py`, `runtime/rag_sync.py`, `runtime/rag_production.py`, `runtime/knowledge_base.py`, `runtime/generation_context.py`, `runtime/damage_workflow_gui.py`, `runtime/assistant/service.py`, and the relevant `knowledge_pipeline` modules and tests.
- Uses the already locked `sentence-transformers==5.7.0` dependency and `BAAI/bge-small-zh-v1.5`; model availability and semantic build failures become explicit production synchronization failures rather than silent lexical-only activation.
- Candidate creation can take longer and use additional disk/GPU/CPU resources because embeddings are rebuilt for the exact activated retrieval corpus.
- No credential is added or persisted. External search remains capability-injected and cannot be reported as successful without verified URL/title sources.
