"""Standalone launcher for the local semantic retrieval module."""
from __future__ import annotations

from pathlib import Path

from test_support import artifact_line, ensure_paths, print_header, read_json, run_entrypoint


QUERY = "混凝土结构加固设计要求"
DOCUMENT_ID = "919efb3636e5de82edda"
INPUT_INDEX_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\embed\semantic.npz")
INPUT_DB_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\index\pipeline.sqlite3")
OUTPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\semantic_retrieve\semantic_retrieval.json")


def main() -> int:
    from knowledge_pipeline.semantic_retrieve import main as semantic_main

    ensure_paths(OUTPUT_PATH)
    print_header("semantic_retrieve", INPUT_INDEX_PATH, OUTPUT_PATH)
    if not INPUT_INDEX_PATH.exists() or not INPUT_DB_PATH.exists():
        print(f"[semantic_retrieve] status=failed error=missing sidecar or database; run test_embed.py and test_index.py first")
        return 1
    code = run_entrypoint(
        "semantic_retrieve",
        semantic_main,
        [QUERY, INPUT_INDEX_PATH, INPUT_DB_PATH, OUTPUT_PATH, "--document-id", DOCUMENT_ID, "--top-k", "6"],
    )
    if code == 0:
        payload = read_json(OUTPUT_PATH)
        print(
            "[semantic_retrieve] summary="
            f"schema={payload.get('schema_version')} query={payload.get('query')} "
            f"mode={payload.get('retrieval_mode')} relevance={payload.get('relevance_status')} "
            f"scope={payload.get('scope_document_ids')} candidates={len(payload.get('candidates') or [])}"
        )
        print(f"[semantic_retrieve] {artifact_line(OUTPUT_PATH)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
