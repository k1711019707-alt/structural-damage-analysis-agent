# Proposal: enable AI tool-calling knowledge retrieval

## Problem
The report and construction-plan workflows currently retrieve knowledge locally before calling the model. The model receives selected chunks but cannot decide when or what to retrieve, so the system is not true agentic/tool-calling RAG.

## Outcome
Add an opt-in tool-calling RAG route in which the model can call a scoped `search_knowledge_base` tool, the desktop executes the local retrieval, and the tool result is returned to the same model turn before structured generation. Preserve source markers, scope restrictions, auditability, streaming status, and the existing deterministic fallback when tool calling is unavailable.

## Scope
- Add a reusable tool-loop transport for Responses API and compatible Chat Completions.
- Expose only the active profile's document/folder scope and bounded query/top_k.
- Support report and construction-plan generation contexts.
- Record tool calls/results in generation audit metadata.
- Keep local pre-retrieval as a compatibility fallback and make the new route configurable.

## Out of scope
- Remote vector database or external web retrieval.
- Removing human review or deterministic evidence constraints.
