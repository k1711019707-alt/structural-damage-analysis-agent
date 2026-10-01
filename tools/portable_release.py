"""Prepare immutable, machine-independent resources for a portable build."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable


TEXT_SUFFIXES = {".json", ".md", ".txt", ".mjs", ".js", ".yaml", ".yml"}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(?:api[_-]?key|apikey|token|secret|password)\s*['\"]?\s*[:=]\s*(['\"])[^'\"\r\n]{8,}\1"),
)
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


class PortableResourceError(RuntimeError):
    """Raised when release resources cannot be staged without ambiguity."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_to(root: Path, value: str | Path, *, label: str) -> tuple[Path, Path]:
    text = str(value or "").strip()
    if not text:
        raise PortableResourceError(f"{label} is missing")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise PortableResourceError(f"{label} escapes the knowledge root: {resolved}") from exc
    return resolved, relative


def _stable_copy(source: Path, target: Path, expected_sha256: str, *, label: str) -> str:
    expected = str(expected_sha256 or "").strip().casefold()
    if not source.is_file():
        raise PortableResourceError(f"{label} is missing: {source}")
    before = sha256_file(source)
    if expected and before.casefold() != expected:
        raise PortableResourceError(f"{label} SHA-256 does not match the active manifest")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    after = sha256_file(source)
    copied = sha256_file(target)
    if before != after or before != copied:
        raise PortableResourceError(f"{label} changed while the release snapshot was being staged")
    return copied


def _portable_basename(value: str) -> str:
    if WINDOWS_ABSOLUTE.match(value) or value.startswith(("\\\\", "//")):
        return PureWindowsPath(value).name
    path = Path(value)
    return path.name if path.is_absolute() else value


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(token in lowered for token in ("api_key", "apikey", "token", "secret", "password")):
                clean[key] = ""
            elif lowered in {"rollback_manifest", "previous"}:
                clean[key] = "" if lowered == "rollback_manifest" else {}
            else:
                clean[key] = _sanitize_value(item)
        return clean
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _portable_basename(value)
    return value


def _sanitize_json_file(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(_sanitize_value(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _sanitize_database(path: Path, document_sources: dict[str, str] | None = None) -> None:
    portable_sources = document_sources or {}
    connection = sqlite3.connect(path)
    try:
        tables = [
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if str(row[0]).startswith("pipeline_")
        ]
        for table in tables:
            columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
            for column in ("source_path", "asset_path"):
                if column not in columns:
                    continue
                rows = connection.execute(f'SELECT rowid,"{column}" FROM "{table}"').fetchall()
                for rowid, value in rows:
                    sanitized = _portable_basename(str(value or ""))
                    if sanitized != str(value or ""):
                        connection.execute(
                            f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',
                            (sanitized, rowid),
                        )
            if table == "pipeline_documents" and {"document_id", "source_path"}.issubset(columns):
                for document_id, portable_path in portable_sources.items():
                    connection.execute(
                        'UPDATE "pipeline_documents" SET "source_path"=? WHERE "document_id"=?',
                        (portable_path, document_id),
                    )
            for column in sorted(name for name in columns if name.endswith("_json")):
                rows = connection.execute(f'SELECT rowid,"{column}" FROM "{table}"').fetchall()
                for rowid, value in rows:
                    try:
                        loaded = json.loads(str(value or "{}"))
                    except json.JSONDecodeError:
                        continue
                    sanitized = json.dumps(_sanitize_value(loaded), ensure_ascii=False, separators=(",", ":"))
                    if sanitized != str(value or ""):
                        connection.execute(
                            f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',
                            (sanitized, rowid),
                        )
            if table == "pipeline_documents" and {"document_id", "metadata_json"}.issubset(columns):
                for document_id, portable_path in portable_sources.items():
                    row = connection.execute(
                        'SELECT "metadata_json" FROM "pipeline_documents" WHERE "document_id"=?',
                        (document_id,),
                    ).fetchone()
                    if not row:
                        continue
                    try:
                        metadata = json.loads(str(row[0] or "{}"))
                    except json.JSONDecodeError:
                        metadata = {}
                    if not isinstance(metadata, dict):
                        metadata = {}
                    metadata["source_path"] = portable_path
                    canonical = metadata.get("canonical_source")
                    if isinstance(canonical, dict):
                        canonical["source_path"] = portable_path
                    connection.execute(
                        'UPDATE "pipeline_documents" SET "metadata_json"=? WHERE "document_id"=?',
                        (json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), document_id),
                    )
        connection.commit()
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("VACUUM")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).casefold() != "ok":
            raise PortableResourceError("staged RAG database failed SQLite integrity_check")
    finally:
        connection.close()


def _copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise PortableResourceError(f"semantic model directory is missing: {source}")
    files = [path for path in source.rglob("*") if path.is_file()]
    if not files:
        raise PortableResourceError(f"semantic model directory is empty: {source}")
    for path in files:
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _stable_copy(path, destination, "", label=f"semantic model file {relative.as_posix()}")


def _model_directory_name(model_name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "--", str(model_name or "semantic-model")).strip("-.")
    return value or "semantic-model"


def _safe_source_name(source_name: str, digest: str, used: dict[str, str]) -> str:
    normalized = PureWindowsPath(str(source_name or "")).name.strip()
    if not normalized or normalized in {".", ".."}:
        normalized = f"knowledge-source-{digest[:12]}.bin"
    key = normalized.casefold()
    existing = used.get(key)
    if existing and existing != digest:
        path = Path(normalized)
        normalized = f"{path.stem}__{digest[:12]}{path.suffix}"
        key = normalized.casefold()
    used[key] = digest
    return normalized


def _resolve_active_source(knowledge_root: Path, item: dict[str, Any]) -> tuple[Path, str]:
    expected = str(item.get("source_sha256") or "").strip().casefold()
    if not expected:
        raise PortableResourceError(
            f"active source document has no SHA-256: {item.get('source_name') or item.get('document_id') or 'unknown'}"
        )
    candidates: list[Path] = []
    configured = str(item.get("source_path") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    source_name = PureWindowsPath(str(item.get("source_name") or configured)).name
    if source_name:
        candidates.append(knowledge_root / "source_files" / source_name)
    seen: set[Path] = set()
    mismatched: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            continue
        if sha256_file(resolved).casefold() == expected:
            return resolved, expected
        mismatched.append(resolved)
    if mismatched:
        raise PortableResourceError(f"active source document SHA-256 mismatch: {source_name or expected[:12]}")
    raise PortableResourceError(f"active source document is missing: {source_name or expected[:12]}")


def _resolve_fhl_script(explicit: str | Path | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("FHL_IMAGE_GEN_SCRIPT", "").strip():
        candidates.append(Path(os.environ["FHL_IMAGE_GEN_SCRIPT"]))
    codex_root = Path(os.environ.get("CODEX_HOME", "").strip() or (Path.home() / ".codex"))
    candidates.extend(sorted(codex_root.glob("plugins/cache/fhl-plugins/fhl-image-gen/*/scripts/generate.mjs"), reverse=True))
    for candidate in candidates:
        if candidate.expanduser().is_file():
            return candidate.expanduser().resolve()
    raise PortableResourceError("FHL Image Gen script was not found; pass --fhl-script")


def _resolve_node(explicit: str | Path | None) -> Path:
    candidates = [Path(explicit)] if explicit else []
    if os.environ.get("FHL_NODE_EXE", "").strip():
        candidates.append(Path(os.environ["FHL_NODE_EXE"]))
    discovered = shutil.which("node") or shutil.which("node.exe")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.expanduser().is_file():
            return candidate.expanduser().resolve()
    raise PortableResourceError("Node executable was not found; pass --node-exe")


def _resolve_semantic_model(explicit: str | Path | None, model_name: str) -> Path:
    if explicit and Path(explicit).expanduser().is_dir():
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("YOLO11_SEMANTIC_MODEL_PATH", "").strip()
    if configured and Path(configured).expanduser().is_dir():
        return Path(configured).expanduser().resolve()
    try:
        from huggingface_hub import snapshot_download

        resolved = snapshot_download(repo_id=model_name, local_files_only=True)
    except Exception as exc:
        raise PortableResourceError(
            f"local semantic model snapshot is unavailable for {model_name}; pass --semantic-model-path"
        ) from exc
    return Path(resolved).resolve()


def _inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold())
        if path.is_file() and path.name != "portable_stage_manifest.json"
    ]


def _assert_no_secrets(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in TEXT_SUFFIXES or path.stat().st_size > 5 * 1024 * 1024:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            raise PortableResourceError(f"staged text contains a credential-like value: {path.relative_to(root)}")


def prepare_portable_resources(
    *,
    project_root: str | Path,
    stage_root: str | Path,
    active_manifest: str | Path,
    fhl_script: str | Path | None = None,
    node_exe: str | Path | None = None,
    semantic_model_path: str | Path | None = None,
    version: str = "",
    health_validator: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    stage = Path(stage_root).resolve()
    manifest_path = Path(active_manifest).resolve()
    knowledge_root = manifest_path.parent.resolve()
    if stage.exists():
        raise PortableResourceError(f"portable staging directory already exists: {stage}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("active") is False:
        raise PortableResourceError("the selected production RAG manifest is not active")

    database, database_relative = _relative_to(
        knowledge_root, payload.get("database_path", ""), label="active RAG database"
    )
    semantic, semantic_relative = _relative_to(
        knowledge_root, payload.get("semantic_index_path", ""), label="active semantic index"
    )
    semantic_manifest = Path(str(semantic) + ".manifest.json")
    semantic_manifest_relative = Path(str(semantic_relative) + ".manifest.json")
    wal = Path(str(database) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise PortableResourceError("active RAG database has a non-empty WAL; wait for the background rebuild to finish")

    validator = health_validator
    if validator is None:
        if str(project) not in sys.path:
            sys.path.insert(0, str(project))
        from runtime.rag_production import inspect_v2_database

        validator = inspect_v2_database
    source_health = validator(
        database,
        expected_sha256=str(payload.get("database_sha256") or ""),
        semantic_index_path=semantic,
        expected_semantic_index_sha256=str(payload.get("semantic_index_sha256") or ""),
        expected_semantic_manifest_sha256=str(payload.get("semantic_manifest_sha256") or ""),
        semantic_root=knowledge_root,
        strict=True,
    )
    if not source_health.get("healthy"):
        raise PortableResourceError(f"active production RAG is unhealthy: {source_health.get('reason', 'unknown')}")

    semantic_payload = json.loads(semantic_manifest.read_text(encoding="utf-8"))
    model_name = str(semantic_payload.get("model_name") or "").strip()
    if not model_name:
        raise PortableResourceError("active semantic manifest does not identify its embedding model")
    resolved_model = _resolve_semantic_model(semantic_model_path, model_name)
    resolved_fhl = _resolve_fhl_script(fhl_script)
    resolved_node = _resolve_node(node_exe)

    try:
        staged_kb = stage / "knowledge_base"
        source_documents = payload.get("source_documents")
        if not isinstance(source_documents, list) or not source_documents:
            raise PortableResourceError("active RAG manifest has no source_documents to bundle")
        staged_sources: list[dict[str, Any]] = []
        document_sources: dict[str, str] = {}
        used_source_names: dict[str, str] = {}
        for raw in source_documents:
            if not isinstance(raw, dict):
                raise PortableResourceError("active RAG source_documents contains a non-object entry")
            source, digest = _resolve_active_source(knowledge_root, raw)
            filename = _safe_source_name(
                str(raw.get("source_name") or source.name), digest, used_source_names
            )
            relative_source = (Path("source_files") / filename).as_posix()
            _stable_copy(
                source,
                staged_kb / relative_source,
                digest,
                label=f"active source document {filename}",
            )
            entry = _sanitize_value(raw)
            entry.update(
                {
                    "source_name": filename,
                    "source_path": relative_source,
                    "source_sha256": digest,
                    "source_bundled": True,
                }
            )
            staged_sources.append(entry)
            document_id = str(raw.get("document_id") or "").strip()
            if document_id:
                document_sources[document_id] = relative_source
        staged_database = staged_kb / database_relative
        staged_semantic = staged_kb / semantic_relative
        staged_semantic_manifest = staged_kb / semantic_manifest_relative
        source_db_sha = _stable_copy(
            database, staged_database, str(payload.get("database_sha256") or ""), label="active RAG database"
        )
        _stable_copy(
            semantic, staged_semantic, str(payload.get("semantic_index_sha256") or ""), label="active semantic index"
        )
        _stable_copy(
            semantic_manifest,
            staged_semantic_manifest,
            str(payload.get("semantic_manifest_sha256") or ""),
            label="active semantic manifest",
        )

        _sanitize_database(staged_database, document_sources)
        _sanitize_json_file(staged_semantic_manifest)
        staged_semantic_payload = json.loads(staged_semantic_manifest.read_text(encoding="utf-8"))
        staged_semantic_payload["model_path"] = ""
        staged_semantic_manifest.write_text(
            json.dumps(staged_semantic_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        portable_db_sha = sha256_file(staged_database)
        portable_semantic_sha = sha256_file(staged_semantic)
        portable_semantic_manifest_sha = sha256_file(staged_semantic_manifest)

        candidate_source = database.parent
        candidate_target = staged_database.parent
        evidence_files: list[str] = []
        for name in ("build_manifest.json", "validation_report.json", "activation_result.json"):
            source = candidate_source / name
            if not source.is_file():
                continue
            target = candidate_target / name
            _stable_copy(source, target, "", label=name)
            _sanitize_json_file(target)
            evidence_files.append(target.relative_to(staged_kb).as_posix())

        staged_payload = _sanitize_value(payload)
        staged_payload.update(
            {
                "database_path": database_relative.as_posix(),
                "database_sha256": portable_db_sha,
                "semantic_index_path": semantic_relative.as_posix(),
                "semantic_index_sha256": portable_semantic_sha,
                "semantic_manifest_path": semantic_manifest_relative.as_posix(),
                "semantic_manifest_sha256": portable_semantic_manifest_sha,
                "source_documents": staged_sources,
                "rollback_manifest": "",
                "previous": {},
                "portable_release_snapshot": {
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "source_database_sha256": source_db_sha,
                    "database_sanitized": portable_db_sha != source_db_sha,
                    "source_documents_bundled": len(staged_sources),
                    "evidence_files": evidence_files,
                },
            }
        )
        validation_evidence = staged_payload.get("validation_evidence")
        if isinstance(validation_evidence, dict):
            validation_evidence["database_sha256"] = portable_db_sha
            validation_evidence["semantic_index_sha256"] = portable_semantic_sha
            validation_evidence["semantic_manifest_sha256"] = portable_semantic_manifest_sha
            report = candidate_target / "validation_report.json"
            validation_evidence["report_path"] = (
                report.relative_to(staged_kb).as_posix() if report.is_file() else ""
            )
            validation_evidence["portable_snapshot"] = True
        staged_active = staged_kb / "active_rag.json"
        staged_active.parent.mkdir(parents=True, exist_ok=True)
        staged_active.write_text(json.dumps(staged_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        portable_health = validator(
            staged_database,
            expected_sha256=portable_db_sha,
            semantic_index_path=staged_semantic,
            expected_semantic_index_sha256=portable_semantic_sha,
            expected_semantic_manifest_sha256=portable_semantic_manifest_sha,
            semantic_root=staged_kb,
            strict=True,
        )
        if not portable_health.get("healthy"):
            raise PortableResourceError(
                f"staged production RAG is unhealthy: {portable_health.get('reason', 'unknown')}"
            )

        model_relative = Path("embedding_models") / _model_directory_name(model_name)
        _copy_tree(resolved_model, stage / model_relative)
        model_manifest = {
            "model_name": model_name,
            "relative_path": model_relative.as_posix(),
            "offline_only": True,
        }
        model_manifest_path = stage / "embedding_models" / "model_manifest.json"
        model_manifest_path.write_text(json.dumps(model_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        _stable_copy(resolved_fhl, stage / "fhl_plugin" / "generate.mjs", "", label="FHL plugin script")
        _stable_copy(resolved_node, stage / "fhl_plugin" / "node.exe", "", label="Node executable")
        runner = project / "packaging" / "fhl_runner.mjs"
        _stable_copy(runner, stage / "fhl_plugin" / "fhl_runner.mjs", "", label="FHL runner")

        _assert_no_secrets(stage)
        stage_manifest = {
            "schema_version": "portable-stage.v1",
            "product_version": str(version or "").strip(),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "active_rag": {
                "manifest": "knowledge_base/active_rag.json",
                "database": f"knowledge_base/{database_relative.as_posix()}",
                "database_sha256": portable_db_sha,
                "semantic_index": f"knowledge_base/{semantic_relative.as_posix()}",
                "semantic_index_sha256": portable_semantic_sha,
                "semantic_manifest": f"knowledge_base/{semantic_manifest_relative.as_posix()}",
                "semantic_manifest_sha256": portable_semantic_manifest_sha,
                "source_documents_bundled": len(staged_sources),
            },
            "semantic_model": model_manifest,
            "fhl": {"script": "fhl_plugin/generate.mjs", "node": "fhl_plugin/node.exe"},
            "files": _inventory(stage),
        }
        stage_manifest_path = stage / "portable_stage_manifest.json"
        stage_manifest_path.write_text(json.dumps(stage_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return stage_manifest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage portable FHL, active RAG, and semantic model resources")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--active-manifest", type=Path, required=True)
    parser.add_argument("--fhl-script", type=Path)
    parser.add_argument("--node-exe", type=Path)
    parser.add_argument("--semantic-model-path", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare_portable_resources(
            project_root=args.project_root,
            stage_root=args.stage_root,
            active_manifest=args.active_manifest,
            fhl_script=args.fhl_script,
            node_exe=args.node_exe,
            semantic_model_path=args.semantic_model_path,
            version=args.version,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error, PortableResourceError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
