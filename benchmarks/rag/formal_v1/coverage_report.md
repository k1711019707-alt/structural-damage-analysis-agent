# 基准覆盖报告

- 状态：formal_candidates_pending_human_review
- 题目数：150
- 唯一直接证据 chunk：155
- 直接证据复用率：0.00%
- 已解析到原 PDF：4/4

## 类型分布

| 类型 | 数量 |
|---|---:|
| exact_identifier | 25 |
| natural_paraphrase | 35 |
| numeric_unit | 25 |
| table_query | 20 |
| multi_evidence | 15 |
| scope_isolation | 10 |
| no_answer | 10 |
| risk_negation_exception | 10 |

## 质量边界

- 所有记录均为 `needs_human_review` / `gold_label: false`。
- 候选答案来自索引证据，不等于原 PDF 人工核验答案。
- 只有通过 REVIEW_GUIDE 所述发布门禁的审核副本才能成为 Gold。
