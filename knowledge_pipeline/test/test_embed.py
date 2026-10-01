"""Standalone launcher for the optional local embedding sidecar module."""
from __future__ import annotations

from pathlib import Path

from test_support import artifact_line, ensure_paths, print_header, read_json, run_entrypoint


INPUT_DB_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\index\pipeline.sqlite3")
OUTPUT_INDEX_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\embed\semantic.npz")
OUTPUT_MANIFEST_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\embed\semantic_manifest.json")


def main() -> int:
    from knowledge_pipeline.embed import main as embed_main

    ensure_paths(OUTPUT_INDEX_PATH, OUTPUT_MANIFEST_PATH)
    print_header("embed", INPUT_DB_PATH, OUTPUT_INDEX_PATH)
    if not INPUT_DB_PATH.exists():
        print(f"[embed] status=failed error=FileNotFoundError: {INPUT_DB_PATH}; run test_index.py first")
        return 1
    code = run_entrypoint("embed", embed_main, [INPUT_DB_PATH, OUTPUT_INDEX_PATH, "--manifest", OUTPUT_MANIFEST_PATH])
    if code == 0:
        payload = read_json(OUTPUT_MANIFEST_PATH)
        print(
            "[embed] summary="
            f"schema={payload.get('schema_version')} count={payload.get('count')} dimension={payload.get('dimension')} "
            f"model={payload.get('model_name')} status={payload.get('status')}"
        )
        print(f"[embed] {artifact_line(OUTPUT_INDEX_PATH)}")
        print(f"[embed] manifest={OUTPUT_MANIFEST_PATH} exists={OUTPUT_MANIFEST_PATH.exists()} bytes={OUTPUT_MANIFEST_PATH.stat().st_size if OUTPUT_MANIFEST_PATH.exists() else 0}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
