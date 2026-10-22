from __future__ import annotations

import copy
import json
import sys
from types import SimpleNamespace

import pytest

from knowledge_pipeline import rerank


class Scorer:
    def __init__(self, scores):
        self.scores = scores
        self.pairs = []

    def predict(self, pairs, **_kwargs):
        self.pairs = pairs
        return self.scores


def _payload():
    return {
        "query": "裂缝如何处理",
        "chunks": [
            {
                "chunk_id": "c1", "text": "裂缝如何处理 裂缝如何处理",
                "source_marker": "[KB:doc:page:1]",
                "metadata": {"score": -2.0, "fusion_score": 0.2, "anchor": True},
            },
            {
                "chunk_id": "c2", "text": "文档：规范\n5.3.2 修补前复核宽度，单位 mm。",
                "source_marker": "[KB:doc:page:2]",
                "metadata": {"score": -1.0, "fusion_score": 0.01},
            },
        ],
        "retrieval_mode": "hybrid_hierarchical",
        "relevance_status": "hit",
        "scope_available": True,
        "scope_document_ids": ["doc"],
        "anchors": [{"chunk_id": "c2"}],
        "context_groups": [{"anchor_chunk_ids": ["c2"], "chunk_ids": ["c2", "p2"]}],
        "route_diagnostics": {"routing_mode": "adaptive"},
        "status": {"warnings": ["upstream-warning"]},
    }


def test_model_scores_override_heuristics_and_preserve_contract():
    payload = _payload()
    original = copy.deepcopy(payload)
    scorer = Scorer([-3.5, 2.1])

    result = rerank.rerank_payload(payload, top_k=1, reranker=scorer)

    assert result.reranker == "BAAI/bge-reranker-v2-m3"
    assert len(result.chunks) == 1
    assert result.chunks[0]["chunk_id"] == "c2"
    assert result.chunks[0]["rerank_score"] == 2.1
    assert result.chunks[0]["original_score"] == -1.0
    assert result.chunks[0]["source_marker"] == "[KB:doc:page:2]"
    assert result.chunks[0]["metadata"] == payload["chunks"][1]["metadata"]
    assert scorer.pairs == [(payload["query"], item["text"]) for item in payload["chunks"]]
    assert result.status.warnings == ["upstream-warning"]
    assert result.status.stage_version == "rerank.v1"
    for key in ["scope_available", "scope_document_ids", "retrieval_mode", "relevance_status",
                "anchors", "context_groups", "route_diagnostics"]:
        assert getattr(result, key) == payload[key]
    assert payload == original


def test_equal_scores_preserve_retrieval_order():
    payload = _payload()
    payload["chunks"].reverse()
    result = rerank.rerank_payload(payload, reranker=Scorer([0.5, 0.5]))
    assert [item["chunk_id"] for item in result.chunks] == ["c2", "c1"]
    assert [item["rank"] for item in result.chunks] == [1, 2]


def test_empty_candidates_do_not_load_model(monkeypatch):
    def fail(*_args):
        pytest.fail("Empty retrieval must not load the model")

    monkeypatch.setattr(rerank, "_load_model", fail)
    result = rerank.rerank_payload({"query": "裂缝", "chunks": []})
    assert result.chunks == []
    assert result.status.warnings == []


def test_load_failure_uses_original_rules_with_warning(monkeypatch):
    def fail(*_args):
        raise OSError("weights unavailable")

    monkeypatch.setattr(rerank, "_load_model", fail)
    result = rerank.rerank_payload(_payload(), top_k=1)

    assert result.reranker == "deterministic-baseline"
    assert result.chunks[0]["chunk_id"] == "c1"
    assert result.chunks[0]["rerank_score"] == 33.0
    assert result.status.warnings[0] == "upstream-warning"
    assert "OSError: weights unavailable" in result.status.warnings[-1]
    assert "using deterministic-baseline" in result.status.warnings[-1]


@pytest.mark.parametrize("scores", [[1.0], [float("nan"), 1.0], [float("inf"), 1.0]])
def test_invalid_model_scores_fall_back_without_losing_candidates(scores):
    result = rerank.rerank_payload(_payload(), reranker=Scorer(scores))
    assert result.reranker == "deterministic-baseline"
    assert len(result.chunks) == 2
    assert "ValueError" in result.status.warnings[-1]


def test_inference_failure_reports_fallback():
    class BrokenScorer:
        def predict(self, *_args, **_kwargs):
            raise RuntimeError("CUDA out of memory")

    result = rerank.rerank_payload(_payload(), reranker=BrokenScorer())
    assert result.reranker == "deterministic-baseline"
    assert "CUDA out of memory" in result.status.warnings[-1]


def test_local_configuration_and_explicit_overrides(monkeypatch, tmp_path):
    calls = []

    def load(path, device):
        calls.append((path, device))
        return Scorer([0.1, 0.9])

    monkeypatch.setattr(rerank, "_load_model", load)
    model_path = tmp_path / "bge"
    monkeypatch.setenv("YOLO11_RERANKER_MODEL_PATH", str(model_path))
    monkeypatch.setenv("YOLO11_RERANKER_DEVICE", "cpu")
    rerank.rerank_payload(_payload())
    rerank.rerank_payload(_payload(), model_path=str(tmp_path / "other"), device="cuda:0")
    assert calls == [(str(model_path), "cpu"), (str(tmp_path / "other"), "cuda:0")]


@pytest.mark.parametrize("cuda", [False, True])
def test_loader_reuses_model_and_loads_explicit_weights_offline(monkeypatch, tmp_path, cuda):
    calls = []

    def cross_encoder(name, **kwargs):
        calls.append((name, kwargs))
        return Scorer([0.1])

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda), float16="float16",
        nn=SimpleNamespace(Identity=lambda: "identity"),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=cross_encoder))
    rerank._load_model.cache_clear()
    try:
        path = str(tmp_path / "bge")
        first = rerank._load_model(path, None)
        assert rerank._load_model(path, None) is first
        assert len(calls) == 1
        assert calls[0][0] == path
        assert calls[0][1]["local_files_only"] is True
        assert calls[0][1]["activation_fn"] == "identity"
        assert calls[0][1]["device"] == ("cuda" if cuda else "cpu")
        if cuda:
            assert calls[0][1]["model_kwargs"] == {"torch_dtype": "float16"}
        else:
            assert "model_kwargs" not in calls[0][1]
        rerank._load_model(None, "cpu")
        assert calls[-1][0] == "BAAI/bge-reranker-v2-m3"
        assert calls[-1][1]["local_files_only"] is False
        assert calls[-1][1]["device"] == "cpu"
        assert "model_kwargs" not in calls[-1][1]
    finally:
        rerank._load_model.cache_clear()


def test_cli_passes_model_options_and_writes_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(rerank, "_load_model", lambda path, device: Scorer([0.1, 0.9]))
    source = tmp_path / "retrieved.json"
    target = tmp_path / "reranked.json"
    source.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    assert rerank.main([str(source), str(target), "--top-k", "1", "--model-path", str(tmp_path), "--device", "cpu"]) == 0
    result = json.loads(target.read_text(encoding="utf-8"))
    assert result["schema_version"] == "knowledge-rerank.v1"
    assert result["reranker"] == "BAAI/bge-reranker-v2-m3"
    assert [item["chunk_id"] for item in result["chunks"]] == ["c2"]
