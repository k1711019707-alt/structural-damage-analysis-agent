## Why

The 150 RAGAS PDF candidates have been manually checked and now need an end-to-end evaluation of the RAG pipeline, including retrieval contexts and generated answers.

## What Changes

- Add a RAGAS 0.4.3 evaluation entry point using the candidate set as input.
- Run scoped retrieval against the indexed SQLite that produced the candidates, then generate answers with the configured project Responses-compatible model.
- Evaluate faithfulness, answer relevancy, context precision, and context recall with RAGAS.
- Preserve per-question retrieval, response, metric, latency, and error evidence; write aggregate reports without changing Gold Set or production artifacts.

## Non-goals

- Do not treat the candidate file as a new Gold Set or overwrite formal benchmark files.
- Do not evaluate against the currently activated production manifest when its document scope does not match these PDFs.
- Do not persist API keys.
