# Specification: AI tool-calling knowledge retrieval

## ADDED Requirements

### Requirement: Model may choose scoped knowledge retrieval
When the AI tool RAG route is enabled, the generation request SHALL expose a `search_knowledge_base` function and SHALL allow the model to request a query before producing the structured report or plan.

#### Scenario: Model requests retrieval
- **WHEN** the model emits a valid search request
- **THEN** the application executes retrieval only within the active profile scope and returns bounded chunks containing source markers and locations

#### Scenario: Invalid scope request
- **WHEN** a tool request attempts to select documents or folders outside the active profile scope
- **THEN** the application ignores the requested scope override and searches only the injected scope, recording a warning

### Requirement: Tool loop remains auditable and bounded
The application SHALL record tool calls, queries, result counts, and source markers, and SHALL stop after a configured maximum number of tool calls.

#### Scenario: Tool limit reached
- **WHEN** the model requests more searches than the configured limit
- **THEN** the application returns a tool error and continues to final structured output or raises a clear generation error

### Requirement: Compatibility fallback
When the endpoint does not support tool calls or tool RAG is disabled, the application SHALL preserve the existing local pre-retrieval generation path.

#### Scenario: Tool route unavailable
- **WHEN** tool calling is disabled or the endpoint rejects the tool request
- **THEN** the application uses the existing scoped local retrieval path and completes generation when that path is available
