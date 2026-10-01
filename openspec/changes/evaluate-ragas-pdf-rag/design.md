# Design

The evaluator reads `benchmarks/rag/ragas_pdf_v1/candidates.jsonl`, requires non-empty question, candidate answer/reference, reference contexts, and document IDs, and uses `knowledge_pipeline/test/results/index/pipeline.sqlite3` as the matching read-only index. Each query is scoped to its candidate document IDs and retrieves top-k chunks through the project's hybrid retrieval entry point.

For each row, a Responses-compatible streaming `ChatOpenAI` instance receives only the retrieved contexts and question; chunks are assembled before scoring. The generated response, retrieved context text/IDs, reference, and RAGAS metric values are persisted in JSONL. RAGAS 0.4.3 evaluates `Faithfulness`, `AnswerRelevancy`, `ContextPrecision`, and `ContextRecall`; failures and non-finite scores remain diagnostics and do not fabricate aggregate values.

The output directory is versioned and separate from `formal_v1`, `evaluation_v1`, `evaluation_v2`, and the candidate source directory. The manifest records hashes, interpreter, model category, metric names, scope, and reference boundary.

For endpoint rotation or a one-off connectivity test, `RAGAS_RESPONSES_URL`, `RAGAS_RESPONSES_KEY`, and `RAGAS_RESPONSES_MODEL` override the JSON configuration in process memory only. Their values MUST NOT be written to output, logs, or manifests.
