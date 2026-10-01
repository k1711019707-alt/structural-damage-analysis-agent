"""Production v2 RAG activation, integrity checks, and safe rollback helpers."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import ctypes
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge_pipeline.contracts import INDEX_SCHEMA_VERSION
from knowledge_pipeline.index import INDEX_STAGE_VERSION
from knowledge_pipeline.semantic_retrieve import load_semantic_arrays, load_semantic_manifest
from runtime.app_paths import (
    active_rag_db_path,
    active_rag_manifest_path,
    active_rag_storage_scope,
    active_rag_write_manifest_path,
    resolve_knowledge_portable_path,
    user_knowledge_base_root,
)


MANIFEST_VERSION = "production-rag.v2"
MINIMUM_INDEX_VERSION = INDEX_SCHEMA_VERSION
MINIMUM_INDEX_STAGE_VERSION = INDEX_STAGE_VERSION
MINIMUM_DOCUMENT_QUALITY_SCORE = 0.65
MINIMUM_PAGE_COVERAGE_RATIO = 1.0
SEVERE_WARNING_TOKENS = (
    "ocrfail", "ocrfailed", "ocrfailure", "ocr失败", "ocr识别失败",
    "页面无文本", "页面没有可用原生文本或ocr文本", "页面没有可用原生文本或ocr文本",
    "没有可用原生文本或ocr文本",
    "nousabletext", "encodinganomaly", "编码异常",
)
REQUIRED_TABLE_COLUMNS: dict[str, set[str]] = {
    "pipeline_documents": {
        "document_id", "source_path", "source_name", "source_sha256", "schema_version",
        "index_version", "status", "quality_score", "page_count", "chunk_count",
        "index_fingerprint", "retrieval_corpus_fingerprint", "retrieval_chunk_count",
        "metadata_json", "created_at", "updated_at",
    },
    "pipeline_pages": {"document_id", "page_number", "extraction_methods_json", "chunk_count", "needs_review", "warnings_json"},
    "pipeline_chunks": {
        "chunk_id", "document_id", "location", "text", "text_search", "parent_id",
        "source_marker", "retrieval_role", "content_hash", "standard_number", "metadata_json",
    },
    "pipeline_chunks_fts": {
        "chunk_id", "document_id", "location", "text_search", "heading_text",
        "clause_number", "standard_number", "table_text", "image_text",
    },
    "pipeline_visual_regions": {"document_id", "region_id", "chunk_id", "metadata_json"},
    "pipeline_evidence_links": {"document_id", "evidence_type", "evidence_id", "chunk_id"},
    "pipeline_index_manifest": {"document_id", "payload_json", "updated_at"},
}


def _activation_manifest_path() -> Path:
    """Use the writable user overlay only for frozen execution."""
    if bool(getattr(sys, "frozen", False)):
        return active_rag_write_manifest_path()
    return active_rag_manifest_path()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


class _ExclusiveFileLock:
    STALE_AFTER_SECONDS = 6 * 60 * 60

    def __init__(self, path: str | Path, *, timeout_seconds: float = 30.0) -> None:
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self.fd: int | None = None

    def __enter__(self) -> "_ExclusiveFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"pid={os.getpid()} utc={datetime.now(timezone.utc).isoformat()}".encode("utf-8"))
                return self
            except FileExistsError:
                if self._confirmed_stale():
                    try:
                        self.path.unlink()
                        continue
                    except FileNotFoundError:
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"lock_timeout:{self.path}")
                time.sleep(0.05)

    @staticmethod
    def _pid_alive(pid: int) -> bool | None:
        if pid <= 0:
            return False
        if os.name == "nt":
            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            error = ctypes.windll.kernel32.GetLastError()
            return False if error == 87 else None
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None

    def _confirmed_stale(self) -> bool:
        try:
            text = self.path.read_text(encoding="utf-8")
            pid_match = re.search(r"\bpid=(\d+)\b", text)
            utc_match = re.search(r"\butc=([^\s]+)", text)
            pid = int(pid_match.group(1)) if pid_match else 0
            timestamp = datetime.fromisoformat(utc_match.group(1)) if utc_match else None
            if timestamp is not None and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - timestamp).total_seconds() if timestamp else None
            alive = self._pid_alive(pid) if pid else None
            if alive is True:
                return False
            if alive is False:
                return True
            return bool(age is not None and age >= self.STALE_AFTER_SECONDS)
        except (OSError, ValueError, TypeError, OverflowError):
            # Unparseable locks are only recoverable after a conservative age.
            try:
                return time.time() - self.path.stat().st_mtime >= self.STALE_AFTER_SECONDS
            except OSError:
                return False

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)


def _resolve_portable_path(value: str | Path | None, *, manifest_path: str | Path | None = None) -> Path | None:
    manifest = Path(manifest_path or active_rag_manifest_path())
    return resolve_knowledge_portable_path(value, root=manifest.parent)


def _readonly_sqlite(path: str | Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=5.0)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def _snapshot_sqlite(source: Path, target: Path) -> None:
    """Create a transactionally consistent SQLite snapshot using backup()."""
    with closing(_readonly_sqlite(source)) as source_db:
        with closing(sqlite3.connect(target)) as target_db:
            source_db.backup(target_db)


def _sqlite_wal_path(path: str | Path) -> Path:
    return Path(f"{Path(path)}-wal")


def _sqlite_wal_size(path: str | Path) -> int:
    wal_path = _sqlite_wal_path(path)
    try:
        return int(wal_path.stat().st_size) if wal_path.is_file() else 0
    except OSError:
        # A sidecar that cannot be inspected is not safe to ignore.
        return -1


def _finite_float(value: Any, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite numeric value")
    return number


def _finite_int(value: Any) -> int:
    number = _finite_float(value)
    if number is None or not number.is_integer():
        raise ValueError("invalid integer value")
    return int(number)


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _page_numbers(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({int(item) for item in value if str(item).isdigit() and int(item) > 0})


def _version_compatible(actual: str, minimum: str) -> bool:
    def split(value: str) -> tuple[str, tuple[int, ...]]:
        match = re.fullmatch(r"(.+?)\.v(\d+(?:\.\d+)*)", str(value or ""))
        return (match.group(1), tuple(int(item) for item in match.group(2).split("."))) if match else ("", ())
    actual_family, actual_parts = split(actual)
    minimum_family, minimum_parts = split(minimum)
    length = max(len(actual_parts), len(minimum_parts))
    return bool(actual_family and actual_family == minimum_family and actual_parts + (0,) * (length - len(actual_parts)) >= minimum_parts + (0,) * (length - len(minimum_parts)))


def _normalize_warning(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"^(?:第?\s*\d+\s*页|page\s*\d+)\s*[:：\-—,，.。;；]*", "", text)
    return re.sub(r"[\s\-—_:：,，.。;；!?！？()（）\[\]【】]+", "", text)


def _semantic_health(
    path_value: str | Path | None,
    db: sqlite3.Connection,
    *,
    expected_index_sha256: str = "",
    expected_manifest_sha256: str = "",
    root: str | Path | None = None,
) -> dict[str, Any]:
    path = resolve_knowledge_portable_path(path_value, root=root)
    result: dict[str, Any] = {
        "configured": bool(path_value),
        "healthy": True,
        "path": str(path or ""),
        "index_sha256": "",
        "manifest_path": "",
        "manifest_sha256": "",
    }
    if not path_value:
        return result
    if path is None or not path.is_file():
        return {**result, "healthy": False, "reason": "semantic_index_missing"}
    manifest_path = Path(str(path) + ".manifest.json")
    result["manifest_path"] = str(manifest_path)
    if not manifest_path.is_file():
        return {**result, "healthy": False, "reason": "semantic_manifest_missing"}
    try:
        index_sha256 = sha256_file(path)
        manifest, manifest_sha256 = load_semantic_manifest(
            manifest_path,
            expected_sha256=expected_manifest_sha256,
        )
        expected_index = str(expected_index_sha256 or "").strip()
        if expected_index and index_sha256.casefold() != expected_index.casefold():
            raise ValueError("semantic_index_sha256_mismatch")
        result.update({
            "index_sha256": index_sha256,
            "manifest_sha256": manifest_sha256,
            "manifest": manifest,
        })
        rows = db.execute(
            "SELECT chunk_id,content_hash FROM pipeline_chunks "
            "WHERE retrieval_role='retrieval' AND text_search<>'' ORDER BY document_id,location,chunk_id"
        ).fetchall()
        identity = [(str(row[0]), str(row[1] or "")) for row in rows]
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        result.update({"retrieval_count": len(identity), "source_fingerprint": fingerprint})
        vectors, chunk_ids, content_hashes = load_semantic_arrays(
            path,
            expected_sha256=index_sha256,
        )
        result.update({
            "vectors_shape": list(vectors.shape),
            "chunk_ids_shape": list(chunk_ids.shape),
            "content_hashes_shape": list(content_hashes.shape),
        })
        if any(array.dtype.kind == "O" for array in (vectors, chunk_ids, content_hashes)):
            result.update({"healthy": False, "reason": "semantic_object_dtype_unsafe"})
        elif str(manifest.get("schema_version") or "") != "knowledge-embeddings.v1":
            result.update({"healthy": False, "reason": "semantic_schema_incompatible"})
        elif vectors.ndim != 2 or chunk_ids.ndim != 1 or content_hashes.ndim != 1:
            result.update({"healthy": False, "reason": "semantic_array_shape_invalid"})
        elif vectors.shape[0] != chunk_ids.shape[0] or vectors.shape[0] != content_hashes.shape[0]:
            result.update({"healthy": False, "reason": "semantic_array_count_mismatch"})
        elif _finite_int(manifest.get("count")) != len(identity) or vectors.shape[0] != len(identity):
            result.update({"healthy": False, "reason": "semantic_count_mismatch"})
        elif _finite_int(manifest.get("dimension")) != int(vectors.shape[1]):
            result.update({"healthy": False, "reason": "semantic_dimension_mismatch"})
        elif not manifest.get("source_fingerprint") or str(manifest.get("source_fingerprint")) != fingerprint:
            result.update({"healthy": False, "reason": "semantic_fingerprint_mismatch"})
        elif [(str(chunk_id), str(content_hash)) for chunk_id, content_hash in zip(chunk_ids.tolist(), content_hashes.tolist())] != identity:
            result.update({"healthy": False, "reason": "semantic_corpus_identity_mismatch"})
        elif sha256_file(path) != index_sha256:
            result.update({"healthy": False, "reason": "semantic_index_changed_during_validation"})
        elif sha256_file(manifest_path) != manifest_sha256:
            result.update({"healthy": False, "reason": "semantic_manifest_changed_during_validation"})
    except Exception as exc:
        stable_reason = str(exc) if str(exc).startswith("semantic_") else f"semantic_error:{type(exc).__name__}"
        reason = (
            "semantic_object_dtype_unsafe"
            if stable_reason == "semantic_index_object_dtype_unsafe"
            else stable_reason
        )
        result.update({"healthy": False, "reason": reason})
    return result


def inspect_v2_database(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    semantic_index_path: str | Path | None = None,
    expected_semantic_index_sha256: str | None = None,
    expected_semantic_manifest_sha256: str | None = None,
    semantic_root: str | Path | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Inspect a corpus without mutation; strict mode applies activation gates."""
    target = Path(path)
    result: dict[str, Any] = {
        "path": str(target.resolve()), "exists": target.is_file(), "strict": bool(strict),
        "healthy": False, "structural_healthy": False, "schema_compatible": False,
        "integrity_healthy": False, "metadata_healthy": False,
        "schema_errors": [], "integrity_errors": [], "quality_errors": [], "quality_warnings": [],
    }
    if not target.is_file():
        result.update({"reason": "database_missing", "integrity_errors": ["database_missing"]})
        return result
    wal_size = _sqlite_wal_size(target)
    result["wal"] = {"path": str(_sqlite_wal_path(target)), "size": wal_size, "present": wal_size != 0}
    if wal_size != 0:
        reason = "sqlite_wal_state_unreadable" if wal_size < 0 else "sqlite_wal_present"
        result.update({"reason": reason, "integrity_errors": [reason]})
        return result
    actual_sha = sha256_file(target)
    expected = str(expected_sha256 or "").strip()
    result.update({"sha256": actual_sha, "expected_sha256": expected})
    if expected and actual_sha != expected:
        result["integrity_errors"].append("database_sha256_mismatch")
        result["reason"] = "database_sha256_mismatch"
        return result
    try:
        with closing(_readonly_sqlite(target)) as db:
            tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result["tables"] = sorted(tables)
            missing_tables = sorted(set(REQUIRED_TABLE_COLUMNS) - tables)
            if missing_tables:
                result["schema_errors"].append({"reason": "missing_tables", "tables": missing_tables})
            missing_columns: dict[str, list[str]] = {}
            for table, required in REQUIRED_TABLE_COLUMNS.items():
                if table in tables:
                    missing = sorted(required - _table_columns(db, table))
                    if missing:
                        missing_columns[table] = missing
            if missing_columns:
                result["schema_errors"].append({"reason": "missing_columns", "columns": missing_columns})

            core_available = {"pipeline_documents", "pipeline_chunks", "pipeline_chunks_fts"}.issubset(tables)
            documents = _finite_int(db.execute("SELECT COUNT(*) FROM pipeline_documents").fetchone()[0]) if "pipeline_documents" in tables else 0
            chunks = _finite_int(db.execute("SELECT COUNT(*) FROM pipeline_chunks").fetchone()[0]) if "pipeline_chunks" in tables else 0
            retrieval_children = indexable_children = 0
            if "pipeline_chunks" in tables and {"retrieval_role", "text_search"}.issubset(_table_columns(db, "pipeline_chunks")):
                retrieval_children = _finite_int(db.execute("SELECT COUNT(*) FROM pipeline_chunks WHERE retrieval_role='retrieval'").fetchone()[0])
                indexable_children = _finite_int(db.execute("SELECT COUNT(*) FROM pipeline_chunks WHERE retrieval_role='retrieval' AND text_search<>''").fetchone()[0])
            fts_rows = _finite_int(db.execute("SELECT COUNT(*) FROM pipeline_chunks_fts").fetchone()[0]) if "pipeline_chunks_fts" in tables else 0
            result.update({
                "documents": documents, "chunks": chunks, "retrieval_children": retrieval_children,
                "indexable_retrieval_children": indexable_children, "fts_rows": fts_rows,
                "fts_parity": bool(indexable_children == fts_rows and indexable_children > 0),
            })
            if indexable_children != fts_rows:
                result["integrity_errors"].append("retrieval_fts_count_mismatch")
            if documents <= 0:
                result["integrity_errors"].append("documents_empty")
            if indexable_children <= 0:
                result["integrity_errors"].append("retrieval_children_empty")

            duplicate_groups: list[dict[str, Any]] = []
            document_quality: list[dict[str, Any]] = []
            metadata_counts = {"source_path": 0, "source_name": 0, "source_sha256": 0, "quality_score": 0}
            if "pipeline_documents" in tables:
                columns = _table_columns(db, "pipeline_documents")
                if "source_sha256" in columns:
                    duplicate_groups = [
                        {"source_sha256": str(row[0]), "document_count": _finite_int(row[1]), "document_ids": str(row[2]).split("|")}
                        for row in db.execute(
                            "SELECT source_sha256,COUNT(*),GROUP_CONCAT(document_id,'|') FROM pipeline_documents "
                            "WHERE source_sha256<>'' GROUP BY source_sha256 HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC"
                        ).fetchall()
                    ]
                selectable = [name for name in ("document_id", "source_path", "source_name", "source_sha256", "quality_score", "page_count", "index_version", "metadata_json") if name in columns]
                for row in db.execute(f"SELECT {','.join(selectable)} FROM pipeline_documents").fetchall() if selectable else []:
                    item = dict(zip(selectable, row))
                    for field in ("source_path", "source_name", "source_sha256"):
                        if str(item.get(field) or "").strip():
                            metadata_counts[field] += 1
                    parsed_quality_score = _finite_float(item.get("quality_score"), allow_none=True)
                    if parsed_quality_score is not None:
                        metadata_counts["quality_score"] += 1
                    metadata = _json_object(item.get("metadata_json"))
                    quality = metadata.get("quality_report") if isinstance(metadata.get("quality_report"), dict) else {}
                    page_count = _finite_int(item.get("page_count") if item.get("page_count") is not None else metadata.get("page_count", quality.get("page_count", 0)))
                    failed_pages = _page_numbers(quality.get("failed_pages") or metadata.get("failed_pages"))
                    low_quality_pages = _page_numbers(quality.get("low_quality_pages") or metadata.get("low_quality_pages"))
                    quality_warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else ([quality.get("warnings")] if quality.get("warnings") else [])
                    conversion_warnings = metadata.get("conversion_warnings") if isinstance(metadata.get("conversion_warnings"), list) else ([metadata.get("conversion_warnings")] if metadata.get("conversion_warnings") else [])
                    warning_values = list(dict.fromkeys(str(value) for value in [*quality_warnings, *conversion_warnings] if str(value).strip()))
                    severe_warnings = sorted({value for value in warning_values if any(token in _normalize_warning(value) for token in SEVERE_WARNING_TOKENS)})
                    document_quality.append({
                        "document_id": str(item.get("document_id") or ""), "page_count": page_count,
                        "quality_score": parsed_quality_score, "failed_pages": failed_pages,
                        "failed_page_count": len(failed_pages), "low_quality_pages": low_quality_pages,
                        "low_quality_page_count": len(low_quality_pages),
                        "warning_count": len(warning_values),
                        "warnings": warning_values,
                        "severe_warnings": severe_warnings,
                        "index_version": str(item.get("index_version") or ""),
                    })
            result["duplicate_source_groups"] = duplicate_groups
            result["document_quality"] = document_quality
            result["metadata_coverage"] = {
                field: {"present": count, "total": documents, "ratio": round(count / documents, 6) if documents else 0.0}
                for field, count in metadata_counts.items()
            }
            for field, count in metadata_counts.items():
                if count != documents:
                    result["quality_errors"].append(f"document_{field}_coverage_incomplete")
            if duplicate_groups:
                result["quality_errors"].append("duplicate_source_sha256")
            for item in document_quality:
                if not _version_compatible(item["index_version"], MINIMUM_INDEX_VERSION):
                    result["schema_errors"].append({
                        "reason": "document_index_version_incompatible",
                        "document_id": item["document_id"],
                        "actual": item["index_version"],
                        "required": MINIMUM_INDEX_VERSION,
                    })
                if item["failed_page_count"]:
                    result["quality_errors"].append(f"document_failed_pages:{item['document_id']}")
                    result["quality_warnings"].append({"reason": "document_failed_pages", "document_id": item["document_id"], "count": item["failed_page_count"]})
                if item["low_quality_page_count"]:
                    result["quality_warnings"].append({"reason": "document_low_quality_pages", "document_id": item["document_id"], "count": item["low_quality_page_count"]})
                score = item.get("quality_score")
                if score is not None and float(score) < MINIMUM_DOCUMENT_QUALITY_SCORE:
                    result["quality_errors"].append(f"document_quality_below_threshold:{item['document_id']}")
                if item["severe_warnings"] and not item["failed_page_count"]:
                    result["quality_errors"].append(f"document_severe_conversion_warning:{item['document_id']}")

            if "pipeline_pages" in tables and {"needs_review", "warnings_json"}.issubset(_table_columns(db, "pipeline_pages")):
                page_rows = _finite_int(db.execute("SELECT COUNT(*) FROM pipeline_pages").fetchone()[0])
                per_document_pages = {
                    str(row[0]): _finite_int(row[1])
                    for row in db.execute("SELECT document_id,COUNT(*) FROM pipeline_pages GROUP BY document_id").fetchall()
                }
                result["page_inventory"] = {
                    "rows": page_rows,
                    "needs_review": _finite_int(db.execute("SELECT COUNT(*) FROM pipeline_pages WHERE needs_review<>0").fetchone()[0]),
                    "with_warnings": _finite_int(db.execute("SELECT COUNT(*) FROM pipeline_pages WHERE warnings_json NOT IN ('','[]','{}')").fetchone()[0]),
                    "per_document": per_document_pages,
                }
                for item in document_quality:
                    indexed_pages = per_document_pages.get(item["document_id"], 0)
                    expected_pages = _finite_int(item["page_count"] or 0)
                    ratio = round(indexed_pages / expected_pages, 6) if expected_pages else 0.0
                    item["indexed_page_count"] = indexed_pages
                    item["page_coverage_ratio"] = ratio
                    actual_page_numbers = {
                        _finite_int(row[0]) for row in db.execute(
                            "SELECT page_number FROM pipeline_pages WHERE document_id=?", (item["document_id"],)
                        ).fetchall()
                    }
                    expected_page_numbers = set(range(1, expected_pages + 1))
                    item["page_numbers_complete"] = actual_page_numbers == expected_page_numbers
                    if indexed_pages != expected_pages or ratio < MINIMUM_PAGE_COVERAGE_RATIO or actual_page_numbers != expected_page_numbers:
                        result["quality_errors"].append(f"document_page_coverage_incomplete:{item['document_id']}")
            if "pipeline_index_manifest" in tables and {"document_id", "payload_json"}.issubset(_table_columns(db, "pipeline_index_manifest")):
                for document_id, payload_json in db.execute("SELECT document_id,payload_json FROM pipeline_index_manifest").fetchall():
                    stage_version = str(_json_object(payload_json).get("stage_version") or "")
                    if not _version_compatible(stage_version, MINIMUM_INDEX_STAGE_VERSION):
                        result["schema_errors"].append({
                            "reason": "document_index_stage_incompatible", "document_id": str(document_id),
                            "actual": stage_version, "required": MINIMUM_INDEX_STAGE_VERSION,
                        })
            semantic = _semantic_health(
                semantic_index_path,
                db,
                expected_index_sha256=str(expected_semantic_index_sha256 or ""),
                expected_manifest_sha256=str(expected_semantic_manifest_sha256 or ""),
                root=semantic_root,
            )
            result["semantic"] = semantic
            if not semantic.get("healthy"):
                result["integrity_errors"].append(str(semantic.get("reason") or "semantic_unhealthy"))

            result["structural_healthy"] = bool(core_available and documents > 0 and indexable_children > 0 and fts_rows > 0)
            result["schema_compatible"] = not result["schema_errors"]
            result["integrity_healthy"] = not result["integrity_errors"]
            result["metadata_healthy"] = not result["quality_errors"]
            result["healthy"] = bool(result["structural_healthy"] and (not strict or result["schema_compatible"] and result["integrity_healthy"] and result["metadata_healthy"]))
            if not result["healthy"]:
                if not result["structural_healthy"]:
                    result["reason"] = "schema_or_content_incomplete"
                elif result["schema_errors"]:
                    result["reason"] = "schema_incompatible"
                elif result["integrity_errors"]:
                    result["reason"] = str(result["integrity_errors"][0])
                else:
                    result["reason"] = "quality_metadata_incomplete"
    except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
        result["reason"] = f"database_error:{type(exc).__name__}"
        result["integrity_errors"].append(result["reason"])
    final_wal_size = _sqlite_wal_size(target)
    if final_wal_size != 0:
        reason = "sqlite_wal_state_unreadable" if final_wal_size < 0 else "sqlite_wal_present"
        if reason not in result["integrity_errors"]:
            result["integrity_errors"].append(reason)
        result.update({"healthy": False, "integrity_healthy": False, "reason": reason})
    else:
        try:
            final_sha = sha256_file(target)
        except OSError:
            final_sha = ""
        if final_sha != actual_sha:
            reason = "database_changed_during_inspection"
            if reason not in result["integrity_errors"]:
                result["integrity_errors"].append(reason)
            result.update({"healthy": False, "integrity_healthy": False, "reason": reason})
    return result


def read_active_manifest(path: str | Path | None = None) -> dict[str, Any] | None:
    target = Path(path or active_rag_manifest_path())
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def active_rag_status(manifest_path: str | Path | None = None) -> dict[str, Any]:
    empty_semantic = {
        "configured": False,
        "healthy": True,
        "path": "",
        "index_sha256": "",
        "manifest_path": "",
        "manifest_sha256": "",
    }
    selected_manifest_path = Path(manifest_path).resolve() if manifest_path is not None else active_rag_manifest_path()
    diagnostics = {
        "manifest_path": str(selected_manifest_path.resolve()),
        "storage_scope": active_rag_storage_scope(),
    }
    manifest = read_active_manifest(selected_manifest_path)
    if not manifest:
        return {"active": False, "reason": "manifest_missing", "legacy_fallback": True, "semantic": empty_semantic, **diagnostics}
    if manifest.get("active") is False:
        return {
            "active": False,
            "reason": str(manifest.get("reason") or "explicitly_disabled"),
            "legacy_fallback": False,
            "manifest": manifest,
            "semantic": empty_semantic,
            **diagnostics,
        }
    if not str(manifest.get("database_sha256") or "").strip():
        return {"active": False, "reason": "manifest_database_sha256_missing", "legacy_fallback": True, "manifest": manifest, "semantic": empty_semantic, **diagnostics}
    db_path = _resolve_portable_path(manifest.get("database_path") or active_rag_db_path(), manifest_path=selected_manifest_path)
    if db_path is None:
        return {"active": False, "reason": "database_path_missing", "legacy_fallback": True, "manifest": manifest, "semantic": empty_semantic, **diagnostics}
    semantic_configured = bool(str(manifest.get("semantic_index_path") or "").strip())
    if semantic_configured and not str(manifest.get("semantic_index_sha256") or "").strip():
        return {"active": False, "reason": "manifest_semantic_index_sha256_missing", "legacy_fallback": True, "manifest": manifest, "semantic": empty_semantic, **diagnostics}
    if semantic_configured and not str(manifest.get("semantic_manifest_sha256") or "").strip():
        return {"active": False, "reason": "manifest_semantic_manifest_sha256_missing", "legacy_fallback": True, "manifest": manifest, "semantic": empty_semantic, **diagnostics}
    semantic_path = _resolve_portable_path(manifest.get("semantic_index_path"), manifest_path=selected_manifest_path) if semantic_configured else None
    health = inspect_v2_database(
        db_path,
        expected_sha256=str(manifest.get("database_sha256") or ""),
        semantic_index_path=semantic_path,
        expected_semantic_index_sha256=str(manifest.get("semantic_index_sha256") or ""),
        expected_semantic_manifest_sha256=str(manifest.get("semantic_manifest_sha256") or ""),
        semantic_root=selected_manifest_path.parent,
        strict=True,
    )
    semantic = health.get("semantic") if isinstance(health.get("semantic"), dict) else empty_semantic
    if not health.get("healthy"):
        return {"active": False, "reason": health.get("reason", "database_unhealthy"), "legacy_fallback": True, "manifest": manifest, "database": health, "semantic": semantic, **diagnostics}
    return {"active": True, "reason": "ready", "legacy_fallback": False, "manifest": manifest, "database": health, "semantic": semantic, **diagnostics}


def deactivate_rag(*, reason: str = "catalog_empty") -> dict[str, Any]:
    """Atomically disable production retrieval without falling back to stale data."""
    target_manifest = _activation_manifest_path()
    previous = read_active_manifest(target_manifest) or {}
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "minimum_index_version": MINIMUM_INDEX_VERSION,
        "active": False,
        "reason": str(reason or "explicitly_disabled"),
        "database_path": "",
        "database_sha256": "",
        "documents": 0,
        "chunks": 0,
        "retrieval_children": 0,
        "fts_rows": 0,
        "semantic_index_path": "",
        "semantic_index_sha256": "",
        "semantic_manifest_path": "",
        "semantic_manifest_sha256": "",
        "source_documents": [],
        "previous": previous,
        "deactivated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(target_manifest, manifest)
    return manifest


def activate_rag(
    database_path: str | Path,
    *,
    validation_report_path: str | Path | None = None,
    query_expectations_path: str | Path | None = None,
    scope_expectations_path: str | Path | None = None,
    build_manifest_path: str | Path | None = None,
    legacy_db_path: str | Path | None = None,
    require_v2_scope: bool | None = None,
    semantic_index_path: str | Path | None = None,
    source_documents: list[dict[str, Any]] | None = None,
    rollback_manifest: str | Path | None = None,
) -> dict[str, Any]:
    missing = [
        name for name, value in (
            ("validation_report_path", validation_report_path),
            ("query_expectations_path", query_expectations_path),
            ("scope_expectations_path", scope_expectations_path),
            ("build_manifest_path", build_manifest_path),
            ("legacy_db_path", legacy_db_path),
            ("require_v2_scope", require_v2_scope),
        ) if value is None
    ]
    if missing:
        raise ValueError(f"activate_rag 缺少必需参数：{', '.join(missing)}")
    assert validation_report_path is not None and query_expectations_path is not None
    assert scope_expectations_path is not None and build_manifest_path is not None and legacy_db_path is not None
    assert require_v2_scope is not None
    target_manifest = _activation_manifest_path()
    with _ExclusiveFileLock(target_manifest.parent / ".activation.lock"):
        return _activate_rag_locked(
            database_path,
            validation_report_path=validation_report_path,
            query_expectations_path=query_expectations_path,
            scope_expectations_path=scope_expectations_path,
            build_manifest_path=build_manifest_path,
            legacy_db_path=legacy_db_path,
            require_v2_scope=require_v2_scope,
            semantic_index_path=semantic_index_path,
            source_documents=source_documents,
            rollback_manifest=rollback_manifest,
        )


def _activate_rag_locked(
    database_path: str | Path,
    *,
    validation_report_path: str | Path,
    query_expectations_path: str | Path,
    scope_expectations_path: str | Path,
    build_manifest_path: str | Path,
    legacy_db_path: str | Path,
    require_v2_scope: bool,
    semantic_index_path: str | Path | None,
    source_documents: list[dict[str, Any]] | None,
    rollback_manifest: str | Path | None,
) -> dict[str, Any]:
    validation_path = Path(validation_report_path).resolve()
    authoritative_query_path = Path(query_expectations_path).resolve()
    authoritative_scope_path = Path(scope_expectations_path).resolve()
    authoritative_build_path = Path(build_manifest_path).resolve()
    authoritative_legacy_path = Path(legacy_db_path).resolve()
    validation = _json_object(validation_path.read_text(encoding="utf-8"))
    from scripts.validate_production_rag import REQUIRED_VALIDATION_GATES, VALIDATION_VERSION, _canonical_hash, validate

    if validation.get("validation_version") != VALIDATION_VERSION or validation.get("status") != "ready":
        raise ValueError("激活必须提供 status=ready 的完整候选验证证据")
    if str(validation.get("validation_evidence_hash") or "") != _canonical_hash(validation):
        raise ValueError("验证报告 evidence hash 无效")
    gates = validation.get("gates") if isinstance(validation.get("gates"), dict) else {}
    if any(name not in gates or gates[name] is not True for name in REQUIRED_VALIDATION_GATES):
        raise ValueError("验证报告缺少 required gates 或存在未通过 gate")
    query_results = validation.get("query_results")
    if not isinstance(query_results, list) or not query_results or not all(item.get("passed") is True for item in query_results if isinstance(item, dict)) or len(query_results) != sum(isinstance(item, dict) for item in query_results):
        raise ValueError("验证报告缺少全部通过的 query_results")
    scope_validation = validation.get("scope_validation") if isinstance(validation.get("scope_validation"), dict) else {}
    if scope_validation.get("configured") is not True or scope_validation.get("passed") is not True:
        raise ValueError("验证报告必须包含已配置且通过的 selected scope validation")
    health = inspect_v2_database(database_path, semantic_index_path=semantic_index_path, strict=True)
    if not health.get("healthy"):
        raise ValueError(f"无法激活未通过严格门禁的 v2 数据库：{health.get('reason', 'unknown')}")
    semantic_health = health.get("semantic") if isinstance(health.get("semantic"), dict) else {}
    semantic_configured = bool(semantic_health.get("configured"))
    semantic_source = Path(str(semantic_health.get("path") or "")).resolve() if semantic_configured else None
    semantic_manifest_source = Path(str(semantic_health.get("manifest_path") or "")).resolve() if semantic_configured else None
    validated_database = validation.get("database") if isinstance(validation.get("database"), dict) else {}
    validated_path = Path(str(validated_database.get("path") or "")).resolve() if validated_database.get("path") else None
    if validated_path != Path(database_path).resolve():
        raise ValueError("验证报告数据库路径与候选数据库不一致")
    if str(validated_database.get("sha256") or "") != str(health.get("sha256") or ""):
        raise ValueError("验证报告数据库 SHA-256 与候选数据库不一致")
    inputs = validation.get("validation_inputs") if isinstance(validation.get("validation_inputs"), dict) else {}
    reported_legacy_path = Path(str(inputs.get("legacy_db_path") or "")).resolve()
    query_binding = inputs.get("query_expectations_file") if isinstance(inputs.get("query_expectations_file"), dict) else {}
    scope_binding = inputs.get("scope_expectations_file") if isinstance(inputs.get("scope_expectations_file"), dict) else {}
    build_binding = inputs.get("build_manifest_file") if isinstance(inputs.get("build_manifest_file"), dict) else {}
    reported_query_path = Path(str(query_binding.get("path") or "")).resolve()
    reported_scope_path = Path(str(scope_binding.get("path") or "")).resolve()
    reported_build_path = Path(str(build_binding.get("path") or "")).resolve()
    if (
        reported_query_path != authoritative_query_path
        or reported_scope_path != authoritative_scope_path
        or reported_build_path != authoritative_build_path
        or reported_legacy_path != authoritative_legacy_path
        or bool(inputs.get("require_v2_scope")) != bool(require_v2_scope)
    ):
        raise ValueError("显式激活参数与验证报告绑定不一致")
    bindings = (
        (authoritative_query_path, query_binding),
        (authoritative_scope_path, scope_binding),
        (authoritative_build_path, build_binding),
    )
    if not authoritative_legacy_path.is_file() or any(not path.is_file() or sha256_file(path) != str(binding.get("sha256") or "") for path, binding in bindings):
        raise ValueError("验证报告缺少可重放的查询、build manifest 或 GUI scope 输入")
    initial_digests = {
        "database": sha256_file(database_path),
        "query": sha256_file(authoritative_query_path),
        "scope": sha256_file(authoritative_scope_path),
        "build": sha256_file(authoritative_build_path),
        "legacy": sha256_file(authoritative_legacy_path),
        "validation": sha256_file(validation_path),
    }
    if semantic_configured:
        if semantic_source is None or semantic_manifest_source is None:
            raise ValueError("语义索引路径未解析")
        initial_digests.update({
            "semantic_index": sha256_file(semantic_source),
            "semantic_manifest": sha256_file(semantic_manifest_source),
        })
        if (
            initial_digests["semantic_index"] != str(semantic_health.get("index_sha256") or "")
            or initial_digests["semantic_manifest"] != str(semantic_health.get("manifest_sha256") or "")
        ):
            raise ValueError("语义索引摘要在初始门禁后发生变化")
    with tempfile.TemporaryDirectory(prefix="rag-validation-rerun-", ignore_cleanup_errors=True) as temporary:
        snapshot_root = Path(temporary)
        snapshot_db = snapshot_root / "candidate.sqlite3"
        snapshot_query = snapshot_root / "query.json"
        snapshot_scope = snapshot_root / "scope.json"
        snapshot_build = snapshot_root / "build.json"
        snapshot_legacy = snapshot_root / "legacy.sqlite3"
        snapshot_semantic = snapshot_root / "semantic.npz"
        snapshot_semantic_manifest = Path(str(snapshot_semantic) + ".manifest.json")
        # Byte-for-byte copies are used here because the activation contract is
        # bound to file SHA-256. SQLite backup() may rewrite page layout and
        # therefore cannot be compared to the authoritative file digest.
        shutil.copyfile(Path(database_path), snapshot_db)
        if sha256_file(snapshot_db) != initial_digests["database"]:
            raise ValueError("候选数据库快照摘要与初始摘要不一致")
        shutil.copyfile(authoritative_legacy_path, snapshot_legacy)
        if sha256_file(snapshot_legacy) != initial_digests["legacy"]:
            raise ValueError("legacy 数据库快照摘要与初始摘要不一致")
        for key, source, target in (
            ("query", authoritative_query_path, snapshot_query),
            ("scope", authoritative_scope_path, snapshot_scope),
            ("build", authoritative_build_path, snapshot_build),
        ):
            shutil.copyfile(source, target)
            if sha256_file(target) != initial_digests[key]:
                raise ValueError(f"{key} 快照摘要与初始摘要不一致")
        if semantic_configured:
            assert semantic_source is not None and semantic_manifest_source is not None
            shutil.copyfile(semantic_source, snapshot_semantic)
            if sha256_file(snapshot_semantic) != initial_digests["semantic_index"]:
                raise ValueError("semantic index 快照摘要与初始摘要不一致")
            shutil.copyfile(semantic_manifest_source, snapshot_semantic_manifest)
            if sha256_file(snapshot_semantic_manifest) != initial_digests["semantic_manifest"]:
                raise ValueError("semantic manifest 快照摘要与初始摘要不一致")
        rerun = validate(
            snapshot_db, snapshot_root / "validation.json", query_expectations_path=snapshot_query,
            build_manifest=snapshot_build, expected_sha256=str(health.get("sha256") or ""),
            legacy_db=snapshot_legacy, scope_expectations_path=snapshot_scope,
            require_v2_scope=bool(require_v2_scope),
            semantic_index_path=snapshot_semantic if semantic_configured else None,
        )
        replay_health = inspect_v2_database(
            snapshot_db,
            expected_sha256=initial_digests["database"],
            semantic_index_path=snapshot_semantic if semantic_configured else None,
            expected_semantic_index_sha256=initial_digests.get("semantic_index"),
            expected_semantic_manifest_sha256=initial_digests.get("semantic_manifest"),
            strict=True,
        )
    rerun_gates = rerun.get("gates") if isinstance(rerun.get("gates"), dict) else {}
    if rerun.get("status") != "ready" or any(rerun_gates.get(name) is not True for name in REQUIRED_VALIDATION_GATES):
        raise ValueError("验证报告重放失败，候选状态已变化或证据不可复现")
    if not replay_health.get("healthy"):
        raise ValueError(f"数据库及语义工件重放失败：{replay_health.get('reason', 'unknown')}")
    final_digests = {
        "database": sha256_file(database_path),
        "query": sha256_file(authoritative_query_path),
        "scope": sha256_file(authoritative_scope_path),
        "build": sha256_file(authoritative_build_path),
        "legacy": sha256_file(authoritative_legacy_path),
        "validation": sha256_file(validation_path),
    }
    if semantic_configured:
        assert semantic_source is not None and semantic_manifest_source is not None
        final_digests.update({
            "semantic_index": sha256_file(semantic_source),
            "semantic_manifest": sha256_file(semantic_manifest_source),
        })
    if final_digests != initial_digests or final_digests["database"] != str(health.get("sha256") or ""):
        raise ValueError("激活前最终摘要复核失败，候选或验证输入在重放期间发生变化")
    final_health = inspect_v2_database(
        database_path,
        expected_sha256=initial_digests["database"],
        semantic_index_path=semantic_source,
        expected_semantic_index_sha256=initial_digests.get("semantic_index"),
        expected_semantic_manifest_sha256=initial_digests.get("semantic_manifest"),
        strict=True,
    )
    if not final_health.get("healthy"):
        raise ValueError(f"激活前最终完整性复核失败：{final_health.get('reason', 'unknown')}")
    health = final_health
    semantic_health = health.get("semantic") if isinstance(health.get("semantic"), dict) else {}
    target_manifest = _activation_manifest_path()
    old = read_active_manifest(target_manifest)
    if old and (
        str(old.get("database_sha256", "")) == str(health.get("sha256", ""))
        and str(old.get("semantic_index_sha256", "")) == str(semantic_health.get("index_sha256", ""))
        and str(old.get("semantic_manifest_sha256", "")) == str(semantic_health.get("manifest_sha256", ""))
    ):
        old = None
    manifest = {
        "manifest_version": MANIFEST_VERSION, "minimum_index_version": MINIMUM_INDEX_VERSION,
        "database_path": _portable_path(Path(database_path)), "database_sha256": health.get("sha256", ""),
        "documents": health.get("documents", 0), "chunks": health.get("chunks", 0),
        "retrieval_children": health.get("retrieval_children", 0), "fts_rows": health.get("fts_rows", 0),
        "semantic_index_path": _portable_path(semantic_source) if semantic_source else "",
        "semantic_index_sha256": str(semantic_health.get("index_sha256") or ""),
        "semantic_manifest_path": _portable_path(semantic_manifest_source) if semantic_manifest_source else "",
        "semantic_manifest_sha256": str(semantic_health.get("manifest_sha256") or ""),
        "source_documents": source_documents or [],
        "validation_evidence": {
            "validation_version": validation.get("validation_version", ""),
            "database_sha256": health.get("sha256", ""),
            "semantic_index_sha256": str(semantic_health.get("index_sha256") or ""),
            "semantic_manifest_sha256": str(semantic_health.get("manifest_sha256") or ""),
            "report_path": str(validation_path),
            "validation_evidence_hash": validation.get("validation_evidence_hash", ""),
        },
        "health_gate": {"status": "passed", "schema_compatible": True, "integrity_healthy": True, "metadata_healthy": True},
        "rollback_manifest": str(Path(rollback_manifest).resolve()) if old and rollback_manifest else "",
        "previous": old or {}, "activated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(target_manifest, manifest)
    return manifest


def _portable_path(path: Path) -> str:
    root = _activation_manifest_path().parent.resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())


def rollback_rag() -> dict[str, Any]:
    current = read_active_manifest()
    if not current:
        return {"rolled_back": False, "reason": "manifest_missing"}
    selected_manifest = active_rag_manifest_path()
    target_manifest = _activation_manifest_path()
    if selected_manifest.resolve() != target_manifest.resolve():
        return {"rolled_back": False, "reason": "bundled_baseline_active", "manifest": current}
    previous = current.get("previous")
    if isinstance(previous, dict) and previous.get("database_path"):
        atomic_write_json(target_manifest, previous)
        return {"rolled_back": True, "manifest": previous}
    target = target_manifest
    backup = target.with_name(target.name + ".previous")
    if backup.is_file():
        os.replace(backup, target)
        return {"rolled_back": True, "manifest": read_active_manifest()}
    target.unlink(missing_ok=True)
    fallback = read_active_manifest()
    return {
        "rolled_back": True,
        "manifest": fallback,
        "legacy_fallback": fallback is None,
        "storage_scope": active_rag_storage_scope(),
    }
