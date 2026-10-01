"""Standalone launcher for the current PDF conversion module."""
from __future__ import annotations

from pathlib import Path

from test_support import artifact_line, ensure_paths, print_header, read_json, run_entrypoint


# 直接声明完整路径，打开本脚本即可看到测试输入和输出位置。
INPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\测试文档\混凝土结构设计规范-上-200页纯图片及复杂表格图片.pdf")
OUTPUT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\pdf_convert\conversion.json")


def main() -> int:
    from knowledge_pipeline.pdf_convert import main as convert_main

    ensure_paths(OUTPUT_PATH)
    print_header("pdf_convert", INPUT_PATH, OUTPUT_PATH)
    if not INPUT_PATH.exists():
        print(f"[pdf_convert] status=failed error=FileNotFoundError: {INPUT_PATH}")
        return 1
    code = run_entrypoint("pdf_convert", convert_main, [INPUT_PATH, OUTPUT_PATH, "--offline-docling"])
    if code == 0:
        payload = read_json(OUTPUT_PATH)
        report = payload.get("quality_report") or {}
        print(
            "[pdf_convert] summary="
            f"schema={payload.get('schema_version')} document_id={payload.get('document_id')} "
            f"pages={len(payload.get('pages') or [])} blocks={len(payload.get('blocks') or [])} "
            f"tables={len(payload.get('tables') or [])} images={len(payload.get('images') or [])} "
            f"quality_score={report.get('quality_score')} warnings={len(report.get('warnings') or [])}"
        )
        print(f"[pdf_convert] {artifact_line(OUTPUT_PATH)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
