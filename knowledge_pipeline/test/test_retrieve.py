"""Standalone launcher for deterministic lexical/hybrid retrieval."""
from __future__ import annotations

from pathlib import Path

from test_support import artifact_line, ensure_paths, print_header, read_json, run_entrypoint


QUERY = "SQL Server 数据库"
INPUT_DB_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\index\pipeline.sqlite3")
OUTPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\retrieve\retrieval.json")


def main() -> int:
    from knowledge_pipeline.retrieve import main as retrieve_main

    ensure_paths(OUTPUT_PATH)
    print_header("retrieve", INPUT_DB_PATH, OUTPUT_PATH)
    if not INPUT_DB_PATH.exists():
        print(f"[retrieve] status=failed error=FileNotFoundError: {INPUT_DB_PATH}; run test_index.py first")
        return 1
    code = run_entrypoint("retrieve", retrieve_main, [QUERY, INPUT_DB_PATH, OUTPUT_PATH, "--top-k", "6"])
    if code == 0:
        payload = read_json(OUTPUT_PATH)
        print(
            "[retrieve] summary="
            f"query={payload.get('query')} mode={payload.get('retrieval_mode')} relevance={payload.get('relevance_status')} "
            f"chunks={len(payload.get('chunks') or [])} scope_available={payload.get('scope_available')}"
        )
        print(f"[retrieve] {artifact_line(OUTPUT_PATH)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
