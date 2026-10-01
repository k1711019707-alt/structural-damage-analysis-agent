"""Read-only, relevance-aware validation for an active or candidate v2 corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.retrieve import retrieve
from runtime.rag_production import _finite_int, _readonly_sqlite, active_rag_status, inspect_v2_database


VALIDATION_VERSION = "production-rag-validation.v3"
REQUIRED_VALIDATION_GATES = (
    "database_health", "schema", "integrity", "quality_metadata",
    "no_duplicate_source_sha256", "representative_queries", "build_manifest",
    "selected_scope_available",
)


def _normalize_standard(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("—", "-").upper()


def _canonical_hash(payload: dict[str, Any]) -> str:
    evidence = dict(payload)
    evidence.pop("validation_evidence_hash", None)
    return hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json_array(path: Path, label: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload or not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"{label} 必须是非空 JSON object array")
    return [dict(item) for item in payload]


def _file_binding(path: Path, payload: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path.resolve()), "sha256": _sha256_file(path),
        "item_count": len(payload) if isinstance(payload, list) else 1,
        "content_summary_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
    }


def _query_validation(db_path: Path, expectation: dict[str, Any]) -> dict[str, Any]:
    query = str(expectation.get("query") or "").strip()
    result = retrieve(query, db_path, top_k=6, max_chars=10000, allow_scoped_fallback=False, expansion_mode="subsection")
    document_ids = sorted({chunk.document_id for chunk in result.chunks})
    standards = sorted({
        _normalize_standard(chunk.metadata.get("standard_number"))
        for chunk in result.chunks if chunk.metadata.get("standard_number")
    })
    result_text = " ".join(
        f"{chunk.document_id} {chunk.source_marker} {chunk.text[:300]}" for chunk in result.chunks
    ).casefold()
    document_labels: dict[str, str] = {}
    if document_ids:
        with _readonly_sqlite(db_path) as connection:
            placeholders = ",".join("?" for _ in document_ids)
            rows = connection.execute(
                f"SELECT document_id,source_name,source_path FROM pipeline_documents WHERE document_id IN ({placeholders})",
                document_ids,
            ).fetchall()
            document_labels = {str(row[0]): f"{row[1] or ''} {row[2] or ''}" for row in rows}
    document_label_text = " ".join(document_labels.values()).casefold()
    expected_ids = {str(item) for item in expectation.get("expected_document_ids") or [] if str(item)}
    expected_standards = {_normalize_standard(item) for item in expectation.get("expected_standards") or [] if str(item)}
    expected_tokens = [str(item).casefold() for item in expectation.get("expected_document_tokens") or [] if str(item)]
    failures: list[str] = []
    if not result.chunks:
        failures.append("zero_results")
    if expected_ids and expected_ids.isdisjoint(document_ids):
        failures.append("expected_document_missing")
    if expected_standards and expected_standards.isdisjoint(standards) and not any(value.casefold() in result_text for value in expected_standards):
        failures.append("expected_standard_missing")
    token_match = str(expectation.get("expected_token_match") or "all").lower()
    tokens_match = all(token in document_label_text for token in expected_tokens) if token_match == "all" else any(token in document_label_text for token in expected_tokens)
    if expected_tokens and not tokens_match:
        failures.append("expected_document_token_missing")
    return {
        "query": query,
        "mode": result.retrieval_mode,
        "relevance_status": result.relevance_status,
        "chunk_count": len(result.chunks),
        "anchor_count": len(result.anchors),
        "context_group_count": len(result.context_groups),
        "document_ids": document_ids,
        "document_labels": document_labels,
        "standards": standards,
        "markers": [chunk.source_marker for chunk in result.chunks],
        "expectation": expectation,
        "passed": not failures,
        "failures": failures,
    }


def _scope_validation(db_path: Path, legacy_db: Path, expectation: dict[str, Any], *, require_v2: bool) -> dict[str, Any]:
    name = str(expectation.get("name") or "selected-scope")
    requested_ids = {str(item) for item in expectation.get("document_ids") or [] if str(item)}
    folder_ids = {str(item) for item in expectation.get("folder_ids") or [] if str(item)}
    failures: list[str] = []
    resolved_ready_ids: set[str] = set()
    legacy_available: set[str] = set()
    if not requested_ids and not folder_ids:
        failures.append("scope_empty")
    if not legacy_db.is_file():
        failures.append("legacy_catalog_missing")
    else:
        try:
            with _readonly_sqlite(legacy_db) as connection:
                tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"documents", "folders", "chunks"}.issubset(tables):
                    failures.append("legacy_catalog_schema_invalid")
                else:
                    all_ready_ids = {str(row[0]) for row in connection.execute("SELECT document_id FROM documents WHERE status='ready'").fetchall()}
                    all_folder_ids = {str(row[0]) for row in connection.execute("SELECT folder_id FROM folders").fetchall()}
                    if requested_ids - all_ready_ids:
                        failures.append("scope_invalid_document_ids")
                    if folder_ids - all_folder_ids:
                        failures.append("scope_invalid_folder_ids")
                    selected_folders = set(folder_ids)
                    if folder_ids:
                        rows = connection.execute("SELECT folder_id,parent_id FROM folders").fetchall()
                        children: dict[str, set[str]] = {}
                        for folder_id, parent_id in rows:
                            children.setdefault(str(parent_id or ""), set()).add(str(folder_id))
                        queue = list(folder_ids)
                        while queue:
                            current = queue.pop()
                            for child in children.get(current, set()):
                                if child not in selected_folders:
                                    selected_folders.add(child)
                                    queue.append(child)
                    folder_document_ids: set[str] = set()
                    if selected_folders:
                        folder_document_ids = {
                            str(row[0]) for row in connection.execute(
                                f"SELECT document_id FROM documents WHERE status='ready' AND folder_id IN ({','.join('?' for _ in selected_folders)})",
                                sorted(selected_folders),
                            ).fetchall()
                        }
                    if requested_ids and folder_ids:
                        resolved_ready_ids = requested_ids & folder_document_ids & all_ready_ids
                    elif requested_ids:
                        resolved_ready_ids = requested_ids & all_ready_ids
                    elif folder_ids:
                        resolved_ready_ids = folder_document_ids
                    if resolved_ready_ids:
                        placeholders = ",".join("?" for _ in resolved_ready_ids)
                        legacy_available = {
                            str(row[0]) for row in connection.execute(
                                f"SELECT DISTINCT document_id FROM chunks WHERE document_id IN ({placeholders})", sorted(resolved_ready_ids)
                            ).fetchall()
                        }
        except sqlite3.Error:
            failures.append("legacy_catalog_error")
    v2_available: set[str] = set()
    if resolved_ready_ids:
        with _readonly_sqlite(db_path) as connection:
            placeholders = ",".join("?" for _ in resolved_ready_ids)
            v2_available = {
                str(row[0]) for row in connection.execute(
                    f"SELECT DISTINCT document_id FROM pipeline_chunks WHERE retrieval_role='retrieval' AND text_search<>'' AND document_id IN ({placeholders})",
                    sorted(resolved_ready_ids),
                ).fetchall()
            }
    v2_missing = resolved_ready_ids - v2_available
    unavailable = resolved_ready_ids - v2_available - legacy_available
    if not resolved_ready_ids:
        failures.append("scope_resolves_no_ready_documents")
    if unavailable:
        failures.append("selected_scope_unavailable")
    if require_v2 and v2_missing:
        failures.append("selected_scope_missing_from_v2")
    unexpected = v2_available - resolved_ready_ids
    if unexpected:
        failures.append("scope_broadened")
    if resolved_ready_ids and not v2_missing:
        backend_plan = "v2"
    elif resolved_ready_ids and v2_available and legacy_available:
        backend_plan = "v2_with_legacy_scope_fallback"
    elif resolved_ready_ids and legacy_available == resolved_ready_ids:
        backend_plan = "legacy_scope_fallback"
    else:
        backend_plan = "unavailable"
    return {
        "name": name, "requested_document_ids": sorted(requested_ids), "requested_folder_ids": sorted(folder_ids),
        "resolved_ready_ids": sorted(resolved_ready_ids), "v2_available": sorted(v2_available),
        "v2_missing": sorted(v2_missing), "legacy_available": sorted(legacy_available),
        "unavailable": sorted(unavailable), "unexpected_document_ids": sorted(unexpected),
        "backend_plan": backend_plan, "passed": not failures, "failures": failures,
    }


def validate(
    db_path: Path,
    output: Path,
    *,
    query_expectations_path: Path | None = None,
    build_manifest: Path | None = None,
    expected_sha256: str | None = None,
    legacy_db: Path | None = None,
    scope_expectations_path: Path | None = None,
    require_v2_scope: bool = False,
    semantic_index_path: Path | None = None,
) -> dict[str, object]:
    configuration_failures: list[str] = []
    query_expectations: list[dict[str, Any]] = []
    scope_expectations: list[dict[str, Any]] | None = None
    if query_expectations_path is None or not query_expectations_path.is_file():
        configuration_failures.append("query_expectations_file_missing")
    else:
        try:
            query_expectations = _load_json_array(query_expectations_path, "query expectations")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            configuration_failures.append("query_expectations_file_invalid")
    for index, expectation in enumerate(query_expectations):
        assertions = [expectation.get("expected_document_ids"), expectation.get("expected_standards"), expectation.get("expected_document_tokens")]
        if not str(expectation.get("query") or "").strip() or not any(isinstance(value, list) and any(str(item).strip() for item in value) for value in assertions):
            configuration_failures.append(f"query_expectation_missing_assertion:{index}")
    if scope_expectations_path is not None:
        if not scope_expectations_path.is_file():
            configuration_failures.append("scope_expectations_file_missing")
        else:
            try:
                scope_expectations = _load_json_array(scope_expectations_path, "scope expectations")
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                configuration_failures.append("scope_expectations_file_invalid")
    manifest: dict[str, Any] = {}
    if build_manifest and build_manifest.is_file():
        try:
            parsed = json.loads(build_manifest.read_text(encoding="utf-8"))
            manifest = parsed if isinstance(parsed, dict) else {}
            if not manifest:
                configuration_failures.append("build_manifest_invalid")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            configuration_failures.append("build_manifest_invalid")
    manifest_sha = str(expected_sha256 or (manifest.get("database") or {}).get("sha256") or "")
    health = inspect_v2_database(
        db_path,
        expected_sha256=manifest_sha or None,
        semantic_index_path=semantic_index_path,
        strict=True,
    )
    rows: list[dict[str, Any]] = []
    if health.get("structural_healthy"):
        rows = [_query_validation(db_path, item) for item in query_expectations]
    duplicate_gate = not bool(health.get("duplicate_source_groups"))
    query_gate = bool(not configuration_failures and rows and all(row["passed"] for row in rows))
    scope_rows: list[dict[str, Any]] = []
    if scope_expectations is not None:
        if legacy_db is None:
            scope_rows = [{"name": "selected-scope", "passed": False, "failures": ["legacy_catalog_missing"]}]
        else:
            scope_rows = [_scope_validation(db_path, legacy_db, item, require_v2=require_v2_scope) for item in scope_expectations]
    scope_gate = True if scope_expectations is None else bool(scope_rows and all(row.get("passed") for row in scope_rows))
    manifest_gate = True
    manifest_failures: list[str] = []
    if manifest:
        required_gate = manifest.get("required_gate") or {}
        if not bool(required_gate.get("passed")):
            manifest_gate = False
            manifest_failures.append("build_required_gate_failed")
        try:
            if _finite_int(manifest.get("canonical_document_count") if manifest.get("canonical_document_count") is not None else manifest.get("document_count", 0)) != _finite_int(health.get("documents") or 0):
                manifest_gate = False
                manifest_failures.append("manifest_document_count_mismatch")
        except (TypeError, ValueError, OverflowError):
            manifest_gate = False
            manifest_failures.append("manifest_numeric_invalid")
    elif build_manifest:
        manifest_gate = False
        manifest_failures.append("build_manifest_invalid")
    gates = {
        "database_health": bool(health.get("healthy")),
        "schema": bool(health.get("schema_compatible")),
        "integrity": bool(health.get("integrity_healthy")),
        "quality_metadata": bool(health.get("metadata_healthy")),
        "no_duplicate_source_sha256": duplicate_gate,
        "representative_queries": query_gate,
        "build_manifest": manifest_gate,
        "selected_scope_available": scope_gate,
    }
    report: dict[str, object] = {
        "validation_version": VALIDATION_VERSION,
        "status": "ready" if all(gates.values()) else "failed",
        "gates": gates,
        "database": health,
        "duplicate_source_groups": health.get("duplicate_source_groups", []),
        "quality": {
            "metadata_coverage": health.get("metadata_coverage", {}),
            "documents": health.get("document_quality", []),
            "errors": health.get("quality_errors", []),
            "warnings": health.get("quality_warnings", []),
        },
        "queries": rows,
        "query_results": rows,
        "selected_scopes": scope_rows,
        "legacy_db_path": str(legacy_db.resolve()) if legacy_db else "",
        "scope_validation": {"configured": scope_expectations is not None, "passed": scope_gate, "results": scope_rows},
        "validation_inputs": {
            "query_expectations_file": _file_binding(query_expectations_path, query_expectations) if query_expectations_path and query_expectations_path.is_file() and query_expectations else {},
            "build_manifest_file": _file_binding(build_manifest, manifest) if build_manifest and build_manifest.is_file() and manifest else {},
            "legacy_db_path": str(legacy_db.resolve()) if legacy_db else "",
            "scope_expectations_file": _file_binding(scope_expectations_path, scope_expectations) if scope_expectations_path and scope_expectations_path.is_file() and scope_expectations else {},
            "require_v2_scope": bool(require_v2_scope),
            "expected_sha256": manifest_sha,
        },
        "configuration_failures": configuration_failures,
        "build_manifest_path": str(build_manifest.resolve()) if build_manifest else "",
        "build_manifest_failures": manifest_failures,
        "active": active_rag_status(),
    }
    report["validation_evidence_hash"] = _canonical_hash(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production v2 RAG integrity, quality, and relevance")
    parser.add_argument("db", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--query-expectations", type=Path, required=True, help="External JSON array with query relevance assertions")
    parser.add_argument("--build-manifest", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--legacy-db", type=Path)
    parser.add_argument("--scope-expectations", type=Path, help="JSON array of selected document/folder scopes")
    parser.add_argument("--require-v2-scope", action="store_true")
    parser.add_argument("--semantic-index", type=Path)
    args = parser.parse_args()
    result = validate(
        args.db, args.output, query_expectations_path=args.query_expectations,
        build_manifest=args.build_manifest, expected_sha256=args.expected_sha256,
        legacy_db=args.legacy_db, scope_expectations_path=args.scope_expectations, require_v2_scope=args.require_v2_scope,
        semantic_index_path=args.semantic_index,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
