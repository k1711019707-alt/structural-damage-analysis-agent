"""Standalone launcher for the current structure-aware chunking module."""
from __future__ import annotations

from pathlib import Path

from test_support import artifact_line, ensure_paths, print_header, read_json, run_entrypoint


INPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\pdf_convert\conversion.json")
OUTPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\chunk\chunks.json")


def main() -> int:
    from knowledge_pipeline.chunk import main as chunk_main

    ensure_paths(OUTPUT_PATH)
    print_header("chunk", INPUT_PATH, OUTPUT_PATH)
    if not INPUT_PATH.exists():
        print(f"[chunk] status=failed error=FileNotFoundError: {INPUT_PATH}; run test_pdf_convert.py first")
        return 1
    code = run_entrypoint("chunk", chunk_main, [INPUT_PATH, OUTPUT_PATH])
    if code == 0:
        payload = read_json(OUTPUT_PATH)
        validation = payload.get("validation") or {}
        print(
            "[chunk] summary="
            f"schema={payload.get('schema_version')} document_id={payload.get('document_id')} "
            f"chunks={len(payload.get('chunks') or [])} parents={sum(1 for x in payload.get('chunks') or [] if (x.get('metadata') or {}).get('chunk_level') == 'parent')} "
            f"validation_errors={validation.get('error_count', 0)} validation_warnings={validation.get('warning_count', 0)}"
        )
        print(f"[chunk] {artifact_line(OUTPUT_PATH)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
