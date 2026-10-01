# Design

The generation service receives a knowledge-base search callback and a retrieval mode. When `ai_tool_rag` is enabled, the initial model request contains evidence and the tool definition, but no preselected chunks. If the model emits `search_knowledge_base`, the callback calls the existing scoped production retrieval pipeline, returns bounded chunks with source markers, and the service continues the same conversation until structured JSON is produced. The loop is bounded by a small maximum number of tool calls and rejects invalid scope or query parameters.

Responses API uses function tools and `function_call_output` items. Compatible Chat Completions uses `tools` and `tool_calls` messages. If a gateway does not support tools, the service emits a route status and falls back to the existing local pre-retrieval path.

The callback is injected from the GUI so the knowledge base remains local and profile-scoped. Tool results are included in the generation audit and citation catalog. Existing schema validation, Chinese normalization, review gates, and render checkbox behavior remain unchanged.
