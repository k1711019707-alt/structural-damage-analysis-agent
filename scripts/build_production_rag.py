"""Build and optionally activate a versioned, quality-gated v2 RAG database."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.chunk import chunk_conversion
from knowledge_pipeline.contracts import (
    CONVERSION_SCHEMA_VERSION,
    BlockRecord,
    DocumentConversion,
    ImageRecord,
    PageRecord,
    QualityReport,
    StageStatus,
    TableRecord,
    write_json,
)
from knowledge_pipeline.index import build_index
from knowledge_pipeline.embed import build_embedding_index
from knowledge_pipeline.pdf_convert import PdfConversionOptions, PdfVisionOptions, convert_document, sha256_file
from runtime.rag_production import MINIMUM_DOCUMENT_QUALITY_SCORE, _ExclusiveFileLock, _finite_float, _finite_int, activate_rag, inspect_v2_database
from runtime.rag_production import MINIMUM_PAGE_COVERAGE_RATIO, read_active_manifest
from runtime.app_paths import user_knowledge_base_root


SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx"}
REQUIRED_PDF_STAGE_VERSION = "pdf-convert.v3"


def _assert_fresh_candidate_output(output_dir: Path) -> None:
    output = output_dir.resolve()
    db_path = (output / "knowledge_base_v2.sqlite3").resolve()
    knowledge_root = user_knowledge_base_root().resolve()
    legacy_db = (knowledge_root / "knowledge_base.sqlite3").resolve()
    active = read_active_manifest() or {}
    active_value = str(active.get("database_path") or "").strip()
    active_db = None
    if active_value:
        candidate = Path(active_value).expanduser()
        active_db = (candidate if candidate.is_absolute() else knowledge_root / candidate).resolve()
    protected = {knowledge_root, legacy_db}
    if active_db is not None:
        protected.add(active_db)
        if output == active_db.parent:
            raise ValueError("output_dir 不能使用当前 active RAG 数据库目录")
    if output in protected or db_path in protected:
        raise ValueError("output_dir 不能指向当前 active 或 legacy 数据库路径")
    if db_path.exists():
        raise FileExistsError(f"候选数据库已存在，必须使用新的版本目录：{db_path}")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output_dir 已存在且非空，必须使用新的版本目录：{output}")


def _source_groups(source_dir: Path, *, include_names: set[str] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, list[Path]] = {}
    for source in sorted(source_dir.rglob("*"), key=lambda item: str(item).casefold()):
        if (
            source.is_file()
            and source.suffix.lower() in SUPPORTED_EXTENSIONS
            and (not include_names or source.name in include_names)
        ):
            grouped.setdefault(sha256_file(source), []).append(source)
    groups = []
    for digest, paths in grouped.items():
        aliases = [{"source_name": path.name, "source_path": str(path.resolve())} for path in paths]
        groups.append({"source_sha256": digest, "canonical": paths[0], "aliases": aliases})
    return sorted(groups, key=lambda item: (str(item["canonical"]).casefold(), item["source_sha256"]))


def _page_numbers(values: Any) -> list[int]:
    if not isinstance(values, list):
        return []
    return sorted({int(value) for value in values if str(value).isdigit() and int(value) > 0})


def _load_docling_conversion_cache(
    source: Path,
    digest: str,
    cache_dirs: list[Path],
    *,
    remote_blank_review: bool = False,
) -> DocumentConversion | None:
    """Load only a source-bound, current-schema Docling conversion artifact."""
    expected_source = source.resolve()
    expected_document_id = __import__("hashlib").sha256(f"{expected_source}:{digest}".encode()).hexdigest()[:20]
    for cache_dir in cache_dirs:
        candidate = cache_dir / f"{digest[:20]}.conversion.json"
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            if (
                payload.get("schema_version") != CONVERSION_SCHEMA_VERSION
                or payload.get("source_sha256") != digest
                or Path(str(payload.get("source_path") or "")).resolve() != expected_source
                or payload.get("document_id") != expected_document_id
                or status.get("stage_version") != REQUIRED_PDF_STAGE_VERSION
                or not str(metadata.get("backend") or "").startswith("docling")
                or status.get("status") not in {"ready", "success_with_warnings"}
            ):
                continue
            conversion = DocumentConversion(
                document_id=str(payload["document_id"]),
                source_path=str(payload["source_path"]),
                source_name=str(payload["source_name"]),
                source_sha256=str(payload["source_sha256"]),
                extension=str(payload["extension"]),
                blocks=[BlockRecord(**item) for item in payload.get("blocks") or []],
                pages=[PageRecord(**item) for item in payload.get("pages") or []],
                images=[ImageRecord(**item) for item in payload.get("images") or []],
                tables=[TableRecord(**item) for item in payload.get("tables") or []],
                visual_regions=list(payload.get("visual_regions") or []),
                quality_report=QualityReport(**(payload.get("quality_report") or {})),
                status=StageStatus(**status),
                metadata={**metadata, "conversion_cache_path": str(candidate.resolve())},
                schema_version=str(payload["schema_version"]),
            )
            if remote_blank_review and conversion.quality_report.failed_pages:
                continue
            return conversion
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return None


def _document_manifest(source: Path, digest: str, aliases: list[dict[str, str]], converted: Any, index_result: dict[str, Any]) -> dict[str, Any]:
    quality = converted.quality_report.to_dict()
    pages = [page.to_dict() for page in converted.pages]
    numeric_failure = False
    try:
        page_count = _finite_int(quality.get("page_count") if quality.get("page_count") is not None else len(pages))
    except (TypeError, ValueError, OverflowError):
        page_count = 0
        numeric_failure = True
    failed_pages = _page_numbers(quality.get("failed_pages"))
    low_quality_pages = _page_numbers(quality.get("low_quality_pages"))
    intentional_blank_pages = _page_numbers(quality.get("intentional_blank_pages"))
    try:
        covered_pages = sorted({_finite_int(page.get("page_number")) for page in pages if _finite_int(page.get("page_number")) > 0})
        text_pages = sorted({
            _finite_int(page.get("page_number")) for page in pages if _finite_int(page.get("page_number")) > 0
            and (_finite_int(page.get("native_text_chars") or 0) > 0 or _finite_int(page.get("ocr_text_chars") or 0) > 0)
        })
    except (TypeError, ValueError, OverflowError):
        covered_pages = []
        text_pages = []
        numeric_failure = True
    ocr_warnings = sorted({
        str(warning) for page in pages for warning in (page.get("warnings") or [])
        if "ocr" in str(warning).casefold()
    } | {
        str(warning) for warning in [*(quality.get("warnings") or []), *(converted.status.warnings or [])]
        if "ocr" in str(warning).casefold()
    })
    try:
        quality_score = _finite_float(quality.get("quality_score"), allow_none=True)
        ocr_pages = _finite_int(quality.get("ocr_pages") or 0)
        chunk_count = _finite_int(index_result.get("chunk_count", 0))
        retrieval_chunk_count = _finite_int(index_result.get("retrieval_chunk_count", 0))
    except (TypeError, ValueError, OverflowError):
        quality_score = None
        ocr_pages = chunk_count = retrieval_chunk_count = 0
        required_gate_failures = ["invalid_numeric_metadata"]
    else:
        required_gate_failures = ["invalid_numeric_metadata"] if numeric_failure else []
    if converted.status.status not in {"ready", "success_with_warnings"}:
        required_gate_failures.append("conversion_not_ready")
    if not str((converted.metadata or {}).get("backend") or "").startswith("docling"):
        required_gate_failures.append("docling_backend_required")
    if page_count <= 0:
        required_gate_failures.append("page_inventory_empty")
    if failed_pages:
        required_gate_failures.append("failed_pages_present")
    coverage_ratio = round(len(covered_pages) / page_count, 6) if page_count else 0.0
    if coverage_ratio < MINIMUM_PAGE_COVERAGE_RATIO:
        required_gate_failures.append("page_coverage_below_threshold")
    if quality_score is None:
        required_gate_failures.append("quality_score_missing")
    elif float(quality_score) < MINIMUM_DOCUMENT_QUALITY_SCORE:
        required_gate_failures.append("quality_score_below_threshold")
    if not index_result.get("status") in {"ready", "unchanged"}:
        required_gate_failures.append("index_not_ready")
    return {
        "source_name": source.name,
        "source_path": str(source.resolve()),
        "source_sha256": digest,
        "canonical_source": {"source_name": source.name, "source_path": str(source.resolve())},
        "aliases": aliases,
        "alias_count": len(aliases),
        "duplicate_alias_count": max(0, len(aliases) - 1),
        "document_id": converted.document_id,
        "conversion_status": converted.status.status,
        "conversion_error": converted.status.error,
        "conversion_warnings": sorted({str(item) for item in converted.status.warnings}),
        "page_coverage": {
            "page_count": page_count,
            "covered_page_count": len(covered_pages),
            "covered_pages": covered_pages,
            "text_page_count": len(text_pages),
            "text_pages": text_pages,
            "failed_page_count": len(failed_pages),
            "failed_pages": failed_pages,
            "intentional_blank_page_count": len(intentional_blank_pages),
            "intentional_blank_pages": intentional_blank_pages,
            "low_quality_page_count": len(low_quality_pages),
            "low_quality_pages": low_quality_pages,
            "coverage_ratio": coverage_ratio,
            "required_coverage_ratio": MINIMUM_PAGE_COVERAGE_RATIO,
        },
        "quality_score": quality_score,
        "needs_review": bool(quality.get("needs_review")),
        "ocr_pages": ocr_pages,
        "ocr_warning_count": len(ocr_warnings),
        "ocr_warnings": ocr_warnings,
        "quality_report": quality,
        "chunk_count": chunk_count,
        "retrieval_chunk_count": retrieval_chunk_count,
        "index_status": index_result.get("status", "failed"),
        "required_gate": {"passed": not required_gate_failures, "failures": required_gate_failures},
    }


def build(
    source_dir: Path,
    output_dir: Path,
    *,
    activate: bool = False,
    query_expectations_path: Path | None = None,
    legacy_db: Path | None = None,
    scope_expectations_path: Path | None = None,
    require_v2_scope: bool = False,
    remote_blank_review: bool = False,
    docling_device: str = "auto",
    docling_batch_size: int = 4,
    include_names: set[str] | None = None,
    conversion_cache_dirs: list[Path] | None = None,
    semantic_model_name: str = "BAAI/bge-small-zh-v1.5",
    semantic_model_path: str | None = None,
    semantic_batch_size: int = 32,
    semantic_device: str | None = None,
) -> dict[str, object]:
    if activate and (legacy_db is None or query_expectations_path is None or scope_expectations_path is None):
        raise ValueError("--activate 必须提供 --legacy-db、--query-expectations 和 --scope-expectations")
    _assert_fresh_candidate_output(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_lock_path = output_dir / ".candidate.lock"
    with _ExclusiveFileLock(candidate_lock_path):
        return _build_locked(
            source_dir, output_dir, activate=activate,
            query_expectations_path=query_expectations_path, legacy_db=legacy_db,
            scope_expectations_path=scope_expectations_path, require_v2_scope=require_v2_scope,
            remote_blank_review=remote_blank_review,
            docling_device=docling_device, docling_batch_size=docling_batch_size,
            include_names=include_names,
            conversion_cache_dirs=conversion_cache_dirs or [],
            semantic_model_name=semantic_model_name,
            semantic_model_path=semantic_model_path,
            semantic_batch_size=semantic_batch_size,
            semantic_device=semantic_device,
        )


def _build_locked(
    source_dir: Path,
    output_dir: Path,
    *,
    activate: bool,
    query_expectations_path: Path | None,
    legacy_db: Path | None,
    scope_expectations_path: Path | None,
    require_v2_scope: bool,
    remote_blank_review: bool,
    docling_device: str,
    docling_batch_size: int,
    include_names: set[str] | None,
    conversion_cache_dirs: list[Path],
    semantic_model_name: str,
    semantic_model_path: str | None,
    semantic_batch_size: int,
    semantic_device: str | None,
) -> dict[str, object]:
    existing = [path for path in output_dir.iterdir() if path.name != ".candidate.lock"]
    if existing:
        raise FileExistsError(f"output_dir 已被其他构建占用或已产生内容：{output_dir.resolve()}")
    db_path = output_dir / "knowledge_base_v2.sqlite3"
    converted_dir = output_dir / "converted"
    chunks_dir = output_dir / "chunks"
    converted_dir.mkdir(exist_ok=True)
    chunks_dir.mkdir(exist_ok=True)
    groups = _source_groups(source_dir, include_names=include_names)
    documents: list[dict[str, Any]] = []
    for group in groups:
        source = group["canonical"]
        digest = str(group["source_sha256"])
        converted = _load_docling_conversion_cache(
            source,
            digest,
            conversion_cache_dirs,
            remote_blank_review=remote_blank_review,
        )
        conversion_options = None
        if remote_blank_review:
            conversion_options = PdfConversionOptions(
                remote_blank_review=True,
                docling_device=docling_device,
                docling_batch_size=docling_batch_size,
                vision=PdfVisionOptions.from_gui_settings(blank_page_review=True),
            )
        elif str(docling_device).casefold() != "auto" or int(docling_batch_size) != 4:
            conversion_options = PdfConversionOptions(
                docling_device=docling_device,
                docling_batch_size=max(1, int(docling_batch_size)),
            )
        if converted is None:
            converted = convert_document(source, options=conversion_options) if conversion_options is not None else convert_document(source)
        write_json(str(converted_dir / f"{digest[:20]}.conversion.json"), converted.to_dict())
        chunks = chunk_conversion(converted)
        write_json(str(chunks_dir / f"{digest[:20]}.chunks.json"), chunks)
        result = build_index(chunks, db_path)
        documents.append(_document_manifest(source, digest, group["aliases"], converted, result))
    semantic_path = output_dir / "semantic.npz"
    semantic_error = ""
    try:
        semantic_manifest = build_embedding_index(
            db_path,
            semantic_path,
            model_name=semantic_model_name,
            model_path=semantic_model_path,
            batch_size=max(1, int(semantic_batch_size)),
            device=semantic_device,
        )
    except Exception as exc:
        semantic_error = f"{type(exc).__name__}: {exc}"
        semantic_manifest = {
            "status": "failed",
            "model_name": semantic_model_name,
            "model_path": str(semantic_model_path or ""),
            "device": str(semantic_device or "auto"),
            "error": semantic_error,
        }
    try:
        health = inspect_v2_database(
            db_path,
            semantic_index_path=semantic_path if not semantic_error else None,
            strict=True,
        )
    except TypeError:
        # Compatibility for injected inspectors used by older callers/tests.
        health = inspect_v2_database(db_path, strict=True)
    semantic_healthy = bool(not semantic_error and (health.get("semantic") or {}).get("healthy"))
    document_gate_failures = [
        {"document_id": item["document_id"], "failures": item["required_gate"]["failures"]}
        for item in documents if not item["required_gate"]["passed"]
    ]
    try:
        database_document_count = _finite_int(health.get("documents") or 0)
    except (TypeError, ValueError, OverflowError):
        database_document_count = -1
    required_gate = {
        "passed": bool(
            documents and health.get("healthy") and not document_gate_failures
            and database_document_count == len(documents)
            and semantic_healthy
        ),
        "database_healthy": bool(health.get("healthy")),
        "schema_compatible": bool(health.get("schema_compatible")),
        "integrity_healthy": bool(health.get("integrity_healthy")),
        "metadata_healthy": bool(health.get("metadata_healthy")),
        "corpus_document_count_match": database_document_count == len(documents),
        "semantic_healthy": semantic_healthy,
        "semantic_error": semantic_error,
        "document_failures": document_gate_failures,
    }
    report: dict[str, object] = {
        "build_version": "production-rag-build.v3",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "database": health,
        "semantic": {
            **semantic_manifest,
            "index_path": str(semantic_path.resolve()),
            "manifest_path": str(Path(str(semantic_path) + ".manifest.json").resolve()),
            "health": dict(health.get("semantic") or {}),
        },
        "documents": documents,
        "source_file_count": sum(len(group["aliases"]) for group in groups),
        "canonical_document_count": len(documents),
        "duplicate_alias_count": sum(max(0, len(group["aliases"]) - 1) for group in groups),
        "document_count": len(documents),
        "required_gate": required_gate,
        "status": "ready" if required_gate["passed"] else "failed",
        "active_manifest_touched": False,
    }
    manifest_path = output_dir / "build_manifest.json"
    write_json(str(manifest_path), report)
    outcome = dict(report)
    if activate:
        activation_result_path = output_dir / "activation_result.json"
        if report["status"] != "ready":
            activation_result = {"activated": False, "reason": "required_gate_failed"}
        else:
            from scripts.validate_production_rag import validate

            validation_path = output_dir / "validation_report.json"
            validation = validate(
                db_path, validation_path, query_expectations_path=query_expectations_path,
                build_manifest=manifest_path, legacy_db=legacy_db,
                scope_expectations_path=scope_expectations_path, require_v2_scope=require_v2_scope,
                semantic_index_path=semantic_path,
            )
            if validation.get("status") != "ready":
                activation_result = {"activated": False, "reason": "candidate_validation_failed", "validation_report": str(validation_path.resolve())}
            else:
                try:
                    manifest = activate_rag(
                        db_path,
                        validation_report_path=validation_path,
                        query_expectations_path=query_expectations_path,
                        scope_expectations_path=scope_expectations_path,
                        build_manifest_path=manifest_path,
                        legacy_db_path=legacy_db,
                        require_v2_scope=require_v2_scope,
                        source_documents=documents,
                        semantic_index_path=semantic_path,
                    )
                    activation_result = {"activated": True, "manifest": manifest, "validation_report": str(validation_path.resolve())}
                except (OSError, ValueError, TypeError, TimeoutError) as exc:
                    activation_result = {
                        "activated": False,
                        "reason": "activation_rejected",
                        "error": f"{type(exc).__name__}: {exc}",
                        "validation_report": str(validation_path.resolve()),
                    }
        write_json(str(activation_result_path), activation_result)
        outcome["activation_result"] = activation_result
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a versioned, strictly gated production v2 RAG database")
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--activate", action="store_true", help="Atomically activate only after every required gate passes")
    parser.add_argument("--query-expectations", type=Path, help="External query expectations JSON array")
    parser.add_argument("--legacy-db", type=Path, help="Optional GUI legacy catalog for selected-scope validation")
    parser.add_argument("--scope-expectations", type=Path, help="Optional selected-scope expectations JSON array")
    parser.add_argument("--require-v2-scope", action="store_true")
    parser.add_argument("--remote-blank-review", action="store_true", help="Use the current GUI Responses API for deterministic blank-page candidates")
    parser.add_argument("--docling-device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--docling-batch-size", type=int, default=4)
    parser.add_argument("--include-name", action="append", default=[], help="Include only this source filename; repeat for multiple documents")
    parser.add_argument("--conversion-cache-dir", action="append", type=Path, default=[], help="Reuse only verified pdf-convert.v3 Docling conversion artifacts")
    parser.add_argument("--semantic-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--semantic-model-path")
    parser.add_argument("--semantic-batch-size", type=int, default=32)
    parser.add_argument("--semantic-device", choices=("cpu", "cuda"))
    args = parser.parse_args()
    result = build(
        args.source_dir, args.output_dir, activate=args.activate,
        query_expectations_path=args.query_expectations, legacy_db=args.legacy_db,
        scope_expectations_path=args.scope_expectations, require_v2_scope=args.require_v2_scope,
        remote_blank_review=args.remote_blank_review,
        docling_device=args.docling_device, docling_batch_size=max(1, args.docling_batch_size),
        include_names={str(item) for item in args.include_name if str(item)} or None,
        conversion_cache_dirs=[Path(item).resolve() for item in args.conversion_cache_dir],
        semantic_model_name=args.semantic_model,
        semantic_model_path=args.semantic_model_path,
        semantic_batch_size=max(1, args.semantic_batch_size),
        semantic_device=args.semantic_device,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    activation_result = result.get("activation_result") if isinstance(result.get("activation_result"), dict) else None
    return 0 if result.get("status") == "ready" and (activation_result is None or activation_result.get("activated") is True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
