"""Standalone launcher for the auditable generation-context module."""
from __future__ import annotations

from pathlib import Path

from test_support import artifact_line, ensure_paths, print_header, read_json, run_entrypoint


INPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\rerank\reranked.json")
OUTPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\generate\generation_context.json")


def main() -> int:
    from knowledge_pipeline.generate import main as generate_main

    ensure_paths(OUTPUT_PATH)
    print_header("generate", INPUT_PATH, OUTPUT_PATH)
    if not INPUT_PATH.exists():
        print(f"[generate] status=failed error=FileNotFoundError: {INPUT_PATH}; run test_rerank.py first")
        return 1
    code = run_entrypoint(
        "generate",
        generate_main,
        [INPUT_PATH, OUTPUT_PATH, "--prompt", "按证据生成工程草案", "--generation-mode", "context-only"],
    )
    if code == 0:
        payload = read_json(OUTPUT_PATH)
        print(
            "[generate] summary="
            f"schema={payload.get('schema_version')} query={payload.get('query')} "
            f"retrieved_chunks={len(payload.get('retrieved_chunks') or [])} evidence_groups={len(payload.get('evidence_groups') or [])} "
            f"review_status={payload.get('review_status')} scope_available={payload.get('scope_available')} "
            f"scope={payload.get('scope_document_ids')}"
        )
        print(f"[generate] {artifact_line(OUTPUT_PATH)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
