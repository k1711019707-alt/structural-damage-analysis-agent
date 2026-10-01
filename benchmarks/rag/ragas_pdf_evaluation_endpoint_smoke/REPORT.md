# RAGAS PDF RAG 评估

- 输入记录：1；已生成回答：1；失败：0；已评分：1
- 评估知识库：候选集对应的测试 SQLite，按 candidate_relevant_documents 限定 scope。
- reference 使用候选集 candidate_answer；这不是正式 Gold Set。

## 聚合指标

{
  "scored_count": 1,
  "metric_error": "",
  "mean": {
    "faithfulness": 0.8421052631578947,
    "answer_relevancy": 0.0,
    "context_precision": null,
    "context_recall": 1.0
  },
  "non_finite_count": {
    "faithfulness": 0,
    "answer_relevancy": 0,
    "context_precision": 1,
    "context_recall": 0
  }
}
