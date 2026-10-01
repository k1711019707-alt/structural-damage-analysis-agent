# RAGAS PDF RAG 评估

- 输入记录：3；已生成回答：3；失败：0；已评分：3
- 评估知识库：候选集对应的测试 SQLite，按 candidate_relevant_documents 限定 scope。
- reference 使用候选集 candidate_answer；这不是正式 Gold Set。

## 聚合指标

{
  "scored_count": 3,
  "metric_error": "",
  "mean": {
    "faithfulness": 0.9722222222222222,
    "answer_relevancy": 0.18428853505018536,
    "context_precision": 0.4962962962759567,
    "context_recall": 0.5
  }
}
