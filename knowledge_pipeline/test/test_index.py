"""Standalone launcher for the current SQLite/FTS5 index module."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from test_support import artifact_line, ensure_paths, print_header, read_json, run_entrypoint


INPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\chunk\chunks.json")
OUTPUT_DB_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\index\pipeline.sqlite3")
OUTPUT_MANIFEST_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\index\index_manifest.json")


def main() -> int:
    from knowledge_pipeline.index import main as index_main

    ensure_paths(OUTPUT_DB_PATH, OUTPUT_MANIFEST_PATH)
    print_header("index", INPUT_PATH, OUTPUT_DB_PATH)
    if not INPUT_PATH.exists():
        print(f"[index] status=failed error=FileNotFoundError: {INPUT_PATH}; run test_chunk.py first")
        return 1
    code = run_entrypoint("index", index_main, [INPUT_PATH, OUTPUT_DB_PATH, "--manifest", OUTPUT_MANIFEST_PATH])
    if code == 0:
        payload = read_json(OUTPUT_MANIFEST_PATH)
        with sqlite3.connect(OUTPUT_DB_PATH) as connection:
            chunk_count = connection.execute("SELECT COUNT(*) FROM pipeline_chunks").fetchone()[0]
            retrieval_count = connection.execute("SELECT COUNT(*) FROM pipeline_chunks WHERE retrieval_role='retrieval'").fetchone()[0]
            document_count = connection.execute("SELECT COUNT(*) FROM pipeline_documents").fetchone()[0]
        print(
            "[index] summary="
            f"schema={payload.get('schema_version')} documents={document_count} chunks={chunk_count} "
            f"retrieval_chunks={retrieval_count} status={payload.get('status')} skipped={payload.get('skipped')}"
        )
        print(f"[index] {artifact_line(OUTPUT_DB_PATH)}")
        print(f"[index] manifest={OUTPUT_MANIFEST_PATH} exists={OUTPUT_MANIFEST_PATH.exists()} bytes={OUTPUT_MANIFEST_PATH.stat().st_size if OUTPUT_MANIFEST_PATH.exists() else 0}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
