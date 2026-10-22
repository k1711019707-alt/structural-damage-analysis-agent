"""Local BGE cross-encoder reranking with an explicit deterministic fallback."""
from __future__ import annotations

import argparse
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

from .contracts import RerankResult, StageStatus, read_json, write_json


DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
_MODEL_LOCK = RLock()


@lru_cache(maxsize=1)
def _load_model(model_path: str | None, device: str | None) -> Any:
    try:
        import torch
        from sentence_transformers import CrossEncoder
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("未安装 sentence-transformers，无法执行模型重排") from exc
    resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    kwargs: dict[str, Any] = {}
    if resolved_device.startswith("cuda"):
        kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
    return CrossEncoder(
        model_path or DEFAULT_RERANKER_MODEL,
        device=resolved_device,
        max_length=1024,
        activation_fn=torch.nn.Identity(),
        local_files_only=bool(model_path),
        **kwargs,
    )


def _query_tokens(query: str) -> set[str]:
    return {item.casefold() for item in re.sub(r"[^\w\u4e00-\u9fff]+", " ", query).split() if len(item) >= 2}


def _baseline_scores(query: str, chunks: list[dict[str, Any]]) -> list[float]:
    tokens = _query_tokens(query)
    scores: list[float] = []
    for index, item in enumerate(chunks):
        text = str(item.get("text", ""))
        metadata = item.get("metadata") or {}
        overlap = sum(1 for token in tokens if token in text.casefold())
        semantic = max(-1.0, min(1.0, float(metadata.get("semantic_score", 0.0) or 0.0)))
        fusion = max(0.0, float(metadata.get("fusion_score", 0.0) or 0.0))
        anchor = bool(metadata.get("anchor"))
        expanded = bool(metadata.get("expanded"))
        heading = metadata.get("heading_path") or []
        heading_overlap = sum(1 for value in heading if str(value).casefold() in query.casefold())
        scores.append(float(
            overlap * 10
            + max(0.0, semantic) * 8
            + fusion * 100
            + heading_overlap * 2
            + (3.0 if anchor else 0.0)
            - (1.0 if expanded and not anchor else 0.0)
            - index * 0.001
        ))
    return scores


def rerank_payload(
    payload: dict[str, Any],
    *,
    top_k: int = 6,
    model_path: str | None = None,
    device: str | None = None,
    reranker: Any | None = None,
) -> RerankResult:
    query = str(payload.get("query", ""))
    chunks = list(payload.get("chunks") or [])
    upstream_status = payload.get("status") or {}
    warnings = [str(item) for item in (upstream_status.get("warnings") or []) if str(item)] if isinstance(upstream_status, dict) else []
    reranker_name = DEFAULT_RERANKER_MODEL
    scores: list[float] = []
    if chunks:
        configured_path = str(model_path or os.environ.get("YOLO11_RERANKER_MODEL_PATH", "")).strip()
        configured_device = str(device or os.environ.get("YOLO11_RERANKER_DEVICE", "")).strip()
        try:
            with _MODEL_LOCK:
                model = reranker if reranker is not None else _load_model(
                    str(Path(configured_path).expanduser()) if configured_path else None,
                    configured_device or None,
                )
                scores = [float(value) for value in model.predict(
                    [(query, str(item.get("text", ""))) for item in chunks],
                    batch_size=4,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )]
            if len(scores) != len(chunks) or not all(math.isfinite(value) for value in scores):
                raise ValueError("重排模型返回的分数数量不匹配或包含非有限值")
        except Exception as exc:
            reranker_name = "deterministic-baseline"
            warnings.append(
                f"{DEFAULT_RERANKER_MODEL} unavailable: {type(exc).__name__}: {exc}; "
                "using deterministic-baseline"
            )
            scores = _baseline_scores(query, chunks)

    ranked: list[dict[str, Any]] = []
    for item, score in zip(chunks, scores):
        metadata = item.get("metadata") or {}
        copy = dict(item)
        copy["original_score"] = float(metadata.get("score", item.get("score", 0.0)) or 0.0)
        copy["rerank_score"] = score
        ranked.append(copy)
    if reranker_name == "deterministic-baseline":
        ranked.sort(key=lambda item: (-float(item["rerank_score"]), str(item.get("chunk_id", ""))))
    else:
        ranked.sort(key=lambda item: -float(item["rerank_score"]))
    for index, item in enumerate(ranked[: max(1, int(top_k))], start=1): item["rank"] = index
    return RerankResult(
        query=query,
        chunks=ranked[: max(1, int(top_k))],
        reranker=reranker_name,
        status=StageStatus(status="ready", warnings=warnings, stage_version="rerank.v1"),
        retrieval_mode=str(payload.get("retrieval_mode") or "unknown"),
        relevance_status=str(payload.get("relevance_status") or "unknown"),
        scope_available=bool(payload.get("scope_available", False)),
        scope_document_ids=[str(item) for item in (payload.get("scope_document_ids") or []) if str(item)],
        anchors=[dict(item) for item in (payload.get("anchors") or []) if isinstance(item, dict)],
        context_groups=[dict(item) for item in (payload.get("context_groups") or []) if isinstance(item, dict)],
        route_diagnostics=dict(payload.get("route_diagnostics") or {}),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rerank retrieved knowledge chunks")
    parser.add_argument("input", type=Path); parser.add_argument("output", type=Path); parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--model-path", help="Local BGE reranker directory (no downloads)")
    parser.add_argument("--device", help="Inference device, e.g. cpu or cuda:0")
    args = parser.parse_args(argv)
    write_json(str(args.output), rerank_payload(
        read_json(str(args.input)), top_k=args.top_k, model_path=args.model_path, device=args.device,
    ).to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
