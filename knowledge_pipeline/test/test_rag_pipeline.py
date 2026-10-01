"""Deterministic end-to-end smoke launcher for the RAG knowledge pipeline."""
from __future__ import annotations

import time
from pathlib import Path

from test_support import artifact_line, ensure_paths, read_json


# 端到端脚本也直接声明完整路径，便于单独查看整条文件链路。
INPUT_PDF_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_base\source_files\GB 55034-2022 建筑与市政施工现场安全卫生与职业健康通用规范.pdf")
RESULTS_ROOT_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results")
CONVERSION_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\pdf_convert\conversion.json")
CHUNKS_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\chunk\chunks.json")
DB_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\index\pipeline.sqlite3")
MANIFEST_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\index\index_manifest.json")
RETRIEVAL_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\retrieve\retrieval.json")
RERANKED_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\rerank\reranked.json")
GENERATION_PATH = Path(r"E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\results\generate\generation_context.json")
QUERY = "混凝土结构 加固 设计"


def run_stage(name: str, entrypoint: object, argv: list[object]) -> None:
    started = time.perf_counter()
    code = int(entrypoint([str(item) for item in argv]))  # type: ignore[operator]
    elapsed = time.perf_counter() - started
    print(f"[rag_pipeline] stage={name} status={'success' if code == 0 else 'failed'} elapsed={elapsed:.3f}s exit_code={code}")
    if code != 0:
        raise RuntimeError(f"stage {name} returned exit code {code}")


def main() -> int:
    ensure_paths(CONVERSION_PATH, CHUNKS_PATH, DB_PATH, MANIFEST_PATH, RETRIEVAL_PATH, RERANKED_PATH, GENERATION_PATH)
    print(f"[rag_pipeline] input={INPUT_PDF_PATH}")
    print(f"[rag_pipeline] results_root={RESULTS_ROOT_PATH}")
    if not INPUT_PDF_PATH.exists():
        print(f"[rag_pipeline] status=failed error=FileNotFoundError: {INPUT_PDF_PATH}")
        return 1
    started = time.perf_counter()
    try:
        from knowledge_pipeline.chunk import main as chunk_main
        from knowledge_pipeline.generate import main as generate_main
        from knowledge_pipeline.index import main as index_main
        from knowledge_pipeline.pdf_convert import main as convert_main
        from knowledge_pipeline.rerank import main as rerank_main
        from knowledge_pipeline.retrieve import main as retrieve_main

        run_stage("pdf_convert", convert_main, [INPUT_PDF_PATH, CONVERSION_PATH, "--offline-docling"])
        run_stage("chunk", chunk_main, [CONVERSION_PATH, CHUNKS_PATH])
        run_stage("index", index_main, [CHUNKS_PATH, DB_PATH, "--manifest", MANIFEST_PATH])
        run_stage("retrieve", retrieve_main, [QUERY, DB_PATH, RETRIEVAL_PATH, "--top-k", "6"])
        run_stage("rerank", rerank_main, [RETRIEVAL_PATH, RERANKED_PATH, "--top-k", "6"])
        run_stage("generate", generate_main, [RERANKED_PATH, GENERATION_PATH, "--prompt", "按证据生成工程草案", "--generation-mode", "context-only"])

        conversion = read_json(CONVERSION_PATH)
        chunks = read_json(CHUNKS_PATH)
        retrieval = read_json(RETRIEVAL_PATH)
        generation = read_json(GENERATION_PATH)
        print(
            "[rag_pipeline] summary="
            f"pages={len(conversion.get('pages') or [])} blocks={len(conversion.get('blocks') or [])} "
            f"chunks={len(chunks.get('chunks') or [])} retrieved={len(retrieval.get('chunks') or [])} "
            f"evidence_groups={len(generation.get('evidence_groups') or [])} "
            f"total_elapsed={time.perf_counter() - started:.3f}s semantic_stage=not_run"
        )
        for artifact in (CONVERSION_PATH, CHUNKS_PATH, DB_PATH, MANIFEST_PATH, RETRIEVAL_PATH, RERANKED_PATH, GENERATION_PATH):
            print(f"[rag_pipeline] {artifact_line(artifact)}")
        return 0
    except Exception as exc:
        print(f"[rag_pipeline] status=failed total_elapsed={time.perf_counter() - started:.3f}s error={type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
