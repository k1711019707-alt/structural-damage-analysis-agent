# 人工复核与 Gold 发布说明

本目录的 `candidates.jsonl` 是正式基准候选包，但不是 Gold。自动检索到的 chunk、候选答案和必备事实都可能继承 OCR、表格解析或切片错误。

## 逐题复核

1. 复制 `review_template.jsonl` 为新的版本化审核文件，不要覆盖 `candidates.jsonl`。
2. 按 `evidence[].source_path` 打开原 PDF，并跳转到 `page_numbers`；不要只看 SQLite 文本。
3. 核对页码、条款号、表头/行列、数字、单位、上下限、否定词和例外条件。
4. 将每个候选 chunk 标为 `direct`、`supporting`、`context_only` 或 `irrelevant`。
5. 在 `review.manual_relevant_chunk_ids` 中只保留能够直接回答问题的 chunk；填写 `gold_answer`。
6. 无答案题必须检查 manifest 所列全部 scope 文档，确认确实无答案，再将 `no_answer_verified` 设为 true。
7. 填写 reviewer_id、reviewed_at 和 notes，并将四项 PDF/文本/答案核验布尔值设为 true。

## 发布 Gold

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\formal_rag_benchmark.py publish `
  --review benchmarks\rag\formal_v1\review_working.jsonl `
  --db knowledge_pipeline\test\results\index\pipeline.sqlite3 `
  --output benchmarks\rag\formal_v1\gold_v1
```

发布器会拒绝缺少原 PDF 核验、答案核验、人工直接证据、相关性标签或审核人信息的记录。建议对外报告前采用双人独立标注与冲突仲裁。
