# Design: 嵌入工程问答助手

## Architecture

```text
MainWindow
  -> AssistantDialog
     -> AssistantController
        -> ConversationStore (SQLite, user data only)
        -> ContextResolver (case/report/plan/detection catalog)
        -> SourceRouter (KB/web/model-prior decisions)
        -> ReadonlyToolRegistry
        -> ProviderAdapter / Responses streaming
        -> CitationRenderer + AnswerPolicy
```

The existing GUI remains the owner of report and plan generation. The assistant receives logical IDs and immutable context snapshots rather than arbitrary paths. The assistant may write its own conversation database and diagnostic event records, but it never writes project artifacts.

## Data model

Use a separate SQLite database under `user_data_root()/assistant/conversations.sqlite3` with tables for conversations, messages, context snapshots, and tool events. Add a read-only detection catalog abstraction over run manifests and generated report/plan manifests. A catalog row includes `run_id`, project id, normalized source/result paths, start/finish timestamps, ordinal sequence, evidence hash, report id/hash, and plan id/hash. Findings include image path/name, finding index, damage type, confidence and evidence reference.

## Automatic routing

`ContextResolver` extracts date intervals, ordinal terms, path terms, image names and report/plan references. It queries the detection catalog first. A unique match binds the conversation turn to that run; multiple matches produce a clarification response. `SourceRouter` then prefers structured case evidence, matching report/plan, scoped RAG, web search for freshness-sensitive intent, and model-prior knowledge only as explicitly marked fallback.

No source selector is shown in the normal dialog. The completed answer displays only the conversation text; provenance, retrieval warnings, URL/title/domain/access time, KB markers, and report/plan/evidence references remain persisted in message audit metadata and are not rendered as an `依据详情` section.

Inline knowledge-base citations are part of the answer text, not a separate source-details section. Resolve the exact marker against retrieved chunk metadata and the read-only active `pipeline_documents` catalog; use `source_name` verbatim and only the common verified heading across chunks sharing a page marker. Keep marker/document ID in the source manifest for audit, while rendering the same readable citation for streaming, final, copied, and historical answers. Unknown IDs become an unverified-source label rather than a guessed title. PDF page numbers refer to indexed PDF pages, not unverified printed page labels.

## OpenCode reuse boundary

OpenCode v2 is MIT licensed. Reuse should be limited to compatible session/message concepts, stream event naming, provider abstraction and tool lifecycle ideas. Do not copy or enable terminal, file-edit, arbitrary filesystem, worktree or Git mutation tools. If source is copied, retain the upstream MIT notice and add a third-party attribution document.

## Provider and safety

Reuse the existing Responses provider normalization and API configuration. Add a provider interface for web search rather than assuming the current text provider implements search. Treat documents and web content as untrusted data; tool calls are selected only from the explicit read-only registry. The answer policy marks model-prior material as unverified and preserves `pending_engineer_review` for engineering conclusions.

## Compatibility and fallback

The assistant must continue to answer from current report/plan and scoped lexical RAG when web search is unavailable. It must show a warning for model-prior fallback, never generate fake citations, and never change active RAG, report, plan, or settings files.
