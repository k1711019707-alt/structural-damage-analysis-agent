from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PATH = PROJECT_ROOT / "scripts" / "evaluate_rag_ablation.py"
SPEC = importlib.util.spec_from_file_location("evaluate_rag_ablation", PATH)
assert SPEC and SPEC.loader
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_query_metrics_exact_values() -> None:
    metrics = evaluation.query_metrics(["x", "a", "b", "y"], {"a", "b"})
    assert metrics["hit@1"] == 0.0
    assert metrics["hit@3"] == 1.0
    assert metrics["recall@3"] == 1.0
    assert metrics["precision@3"] == 2 / 3
    assert metrics["mrr@10"] == 0.5
    assert 0.0 < metrics["ndcg@10"] < 1.0
    assert metrics["hit@30"] == 1.0


def test_refusal_metrics() -> None:
    rows = [
        {"expected_no_answer": True, "predicted_no_answer": True},
        {"expected_no_answer": True, "predicted_no_answer": False},
        {"expected_no_answer": False, "predicted_no_answer": True},
        {"expected_no_answer": False, "predicted_no_answer": False},
    ]
    result = evaluation.refusal_metrics(rows)
    assert result["no_answer_precision"] == 0.5
    assert result["no_answer_recall"] == 0.5
    assert result["no_answer_f1"] == 0.5
    assert result["answerable_false_abstention_rate"] == 0.5


def test_direct_ids_do_not_include_expanded_context() -> None:
    result = evaluation.RetrievalResult(
        query="q",
        chunks=[
            evaluation.ChunkRecord("direct", "doc", "page:1", "x", metadata={"retrieval_role": "retrieval"}),
            evaluation.ChunkRecord("parent", "doc", "unit:1", "p", metadata={"retrieval_role": "context_only", "expanded": True}),
        ],
        anchors=[{"chunk_id": "direct"}],
    )
    assert evaluation.direct_ids_from_result(result) == ["direct"]


def test_percentile_interpolates() -> None:
    assert evaluation.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert evaluation.percentile([1], 0.95) == 1.0
