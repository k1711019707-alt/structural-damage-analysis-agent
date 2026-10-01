"""Standalone launcher for the deterministic reranking module."""
from __future__ import annotations

from pathlib import Path

from test_support import artifact_line, ensure_paths, print_header, read_json, run_entrypoint


INPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\retrieve\retrieval.json")
OUTPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\rerank\reranked.json")


def main() -> int:
    from knowledge_pipeline.rerank import main as rerank_main

    ensure_paths(OUTPUT_PATH)
    print_header("rerank", INPUT_PATH, OUTPUT_PATH)
    if not INPUT_PATH.exists():
        print(f"[rerank] status=failed error=FileNotFoundError: {INPUT_PATH}; run test_retrieve.py first")
        return 1
    code = run_entrypoint("rerank", rerank_main, [INPUT_PATH, OUTPUT_PATH, "--top-k", "6"])
    if code == 0:
        payload = read_json(OUTPUT_PATH)
        print(
            "[rerank] summary="
            f"query={payload.get('query')} reranker={payload.get('reranker')} "
            f"chunks={len(payload.get('chunks') or [])} relevance={payload.get('relevance_status')} "
            f"scope_available={payload.get('scope_available')} scope={payload.get('scope_document_ids')}"
        )
        print(f"[rerank] {artifact_line(OUTPUT_PATH)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
