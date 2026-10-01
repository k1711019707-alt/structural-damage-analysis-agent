"""Reranking stage; deterministic baseline with a future adapter seam."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from .contracts import RERANK_SCHEMA_VERSION, RerankResult, StageStatus, read_json, write_json


def _query_tokens(query: str) -> set[str]:
    return {item.casefold() for item in re.sub(r"[^\w\u4e00-\u9fff]+", " ", query).split() if len(item) >= 2}


def rerank_payload(payload: dict[str, Any], *, top_k: int = 6) -> RerankResult:
    query = str(payload.get("query", ""))
    tokens = _query_tokens(query)
    ranked: list[dict[str, Any]] = []
    for index, item in enumerate(payload.get("chunks") or []):
        text = str(item.get("text", ""))
        metadata = item.get("metadata") or {}
        overlap = sum(1 for token in tokens if token in text.casefold())
        original = float(metadata.get("score", item.get("score", 0.0)) or 0.0)
        semantic = max(-1.0, min(1.0, float(metadata.get("semantic_score", 0.0) or 0.0)))
        fusion = max(0.0, float(metadata.get("fusion_score", 0.0) or 0.0))
        anchor = bool(metadata.get("anchor"))
        expanded = bool(metadata.get("expanded"))
        heading = metadata.get("heading_path") or []
        heading_overlap = sum(1 for value in heading if str(value).casefold() in query.casefold())
        # bm25 scores are normally negative in SQLite; use a bounded additive
        # feature while preserving the original score for auditability.
        rerank_score = float(
            overlap * 10
            + max(0.0, semantic) * 8
            + fusion * 100
            + heading_overlap * 2
            + (3.0 if anchor else 0.0)
            - (1.0 if expanded and not anchor else 0.0)
            - index * 0.001
        )
        copy = dict(item)
        copy["original_score"] = original
        copy["rerank_score"] = rerank_score
        copy["rank"] = index + 1
        ranked.append(copy)
    ranked.sort(key=lambda item: (-float(item["rerank_score"]), str(item.get("chunk_id", ""))))
    for index, item in enumerate(ranked[: max(1, int(top_k))], start=1): item["rank"] = index
    upstream_status = payload.get("status") or {}
    warnings = [str(item) for item in (upstream_status.get("warnings") or []) if str(item)] if isinstance(upstream_status, dict) else []
    return RerankResult(
        query=query,
        chunks=ranked[: max(1, int(top_k))],
        reranker="deterministic-baseline",
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
    args = parser.parse_args(argv)
    write_json(str(args.output), rerank_payload(read_json(str(args.input)), top_k=args.top_k).to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
