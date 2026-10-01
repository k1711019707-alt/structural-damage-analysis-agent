## Context

`AssistantContextResolver` currently scans known detection snapshots and returns an `ambiguous` result when several runs match. `AssistantService.stream_answer()` treats that result as terminal and returns a list of batches before querying the knowledge base. This is incorrect for questions such as “裂缝损伤分为几个等级？是如何判定的”, where historical detection runs are incidental context and the active knowledge base should be searched first.

The existing knowledge path already uses `pipeline_search_with_scope(ActiveRagScopeAdapter(), ...)`, preserves active-v2 scope, and distinguishes direct anchors from `scoped_fallback`. The change should reuse that path and only relax the context gate and answer wording. The GUI, conversation persistence, citation resolution, and read-only boundary remain unchanged.

## Goals / Non-Goals

**Goals:**

- Prevent general knowledge questions from being blocked by multiple detection snapshots.
- Make detection results, reports, and plans optional context rather than a prerequisite.
- Preserve knowledge-base-first retrieval and make no-hit routing a normal fallback path.
- Instruct the model to combine relevant references with its own understanding while retaining evidence and freshness warnings.
- Add regression tests for the screenshot case and explicit detection-context cases.

**Non-Goals:**

- Do not change the active-v2 database schema, semantic index, chunking, reranking, or scope activation.
- Do not add a new agent tool protocol or enable file mutation/command execution.
- Do not remove source manifests, citations, engineering review warnings, or credential redaction.

## Decisions

### 1. Resolve explicit context without making it a hard gate

Add a small intent check in `AssistantContextResolver` for explicit detection terms (for example detection result, batch, image, report, plan, date, ordinal, or path). For questions without explicit context selectors, do not turn the complete catalog into an ambiguity result. If a current output directory identifies one run, it may be included; otherwise the resolver returns a non-ambiguous context warning and lets the normal retrieval path continue.

For explicit selectors that match more than one run, include the bounded matching runs as optional context without surfacing an ordinary ambiguity warning. If no run is resolved, return an empty optional context and let the normal retrieval path continue.

### 2. Keep knowledge retrieval first and make fallback non-blocking

`stream_answer()` continues to call `_knowledge_context()` before web routing. `kb_has_answer` remains a routing signal only. A miss, unavailable active RAG, or scoped fallback produces warnings and enables web/model-prior fallback; it does not raise a user-facing failure.

### 3. Separate answer permission from evidence confidence

Update the system instructions so that the model may answer every question, but must distinguish direct project evidence, knowledge-base references, web evidence, and general model knowledge. It should use the knowledge-base text when present and add its own explanation, not merely copy chunks. Existing prohibitions on fabricated facts, fake citations, commands, file mutation, and credential disclosure remain.

### 4. Preserve manifest semantics

Keep `source_mode`, `external_fallback_reason`, `warnings`, `sources`, `web_search_used`, `trace_id`, and `cancelled`. Do not add an ordinary unresolved/ambiguous batch warning or return a special terminal `detection_catalog` answer. Existing citation rendering remains unchanged.

### Alternatives considered

- **Always inject every historical detection run:** rejected because unrelated runs can dominate the prompt and exceed context limits.
- **Delete the detection catalog feature:** rejected because explicit questions about a report, image, or historical run still benefit from deterministic context resolution.
- **Let the model decide whether to search the KB:** rejected because the project requires deterministic active-v2 scope enforcement before generation.

## Risks / Trade-offs

- [Risk] A user may ask an explicit detection question without enough selectors. → Keep a warning and bounded candidate metadata, but allow a general answer instead of blocking.
- [Risk] General model answers may be mistaken for standards-based conclusions. → Preserve model-prior/freshness warnings and engineer-review instructions.
- [Risk] Including a current run for a generic question may add irrelevant context. → Only include it when the current output is uniquely identified; otherwise provide no detection body.

## Migration Plan

1. Update the resolver and service implementation.
2. Add focused unit tests and run the assistant/GUI contract tests.
3. Validate the OpenSpec change and deploy with the existing conversation and active-RAG files unchanged.
4. Rollback by reverting the two runtime files and tests; no data migration is required.

## Open Questions

- Whether a future release should add relevance-ranked retrieval over historical detection findings as a separate corpus. This change keeps the existing bounded snapshot mechanism.
