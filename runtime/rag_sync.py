"""GUI-triggered production RAG rebuild, validation, and activation."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runtime.app_paths import production_rag_write_root, project_knowledge_base_root


Progress = Callable[[str], None]


def _production_build_root() -> Path:
    return production_rag_write_root() if bool(getattr(sys, "frozen", False)) else project_knowledge_base_root()


def _snapshot_catalog(source: Path, target: Path) -> Path:
    """Create one transactionally consistent catalog for build and validation."""
    if not source.is_file():
        raise FileNotFoundError(f"知识库目录数据库不存在：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    return target


def _snapshot_ready_records(snapshot: Path) -> list[Any]:
    from types import SimpleNamespace

    with sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT document_id,name,path,status FROM documents WHERE status='ready' ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [
        SimpleNamespace(document_id=str(row[0]), name=str(row[1]), path=str(row[2]), status=str(row[3]))
        for row in rows if Path(str(row[2])).is_file()
    ]


def user_facing_sync_error(exc: Exception) -> str:
    """Keep GUI errors actionable while retaining the validation report path."""
    raw = str(exc)
    report = re.search(r"['\"]validation_report['\"]\s*:\s*['\"]([^'\"]+)", raw)
    if "candidate_validation_failed" in raw:
        suffix = f" 诊断报告：{report.group(1)}" if report else ""
        return f"生产 RAG 候选校验未通过，未替换当前知识库。{suffix}".strip()
    return f"生产 RAG 自动同步失败：{type(exc).__name__}: {raw}"


def _write_expectations(path: Path, records: list[Any], *, scopes: bool = False) -> None:
    payload: list[dict[str, Any]] = []
    for record in records:
        document_id = str(getattr(record, "document_id", "") or "")
        name = str(getattr(record, "name", "") or "")
        if not document_id:
            continue
        if scopes:
            payload.append({"name": f"gui-{document_id}", "document_ids": [document_id]})
        else:
            # Source-title matching is deterministic and does not invent domain facts.
            query = Path(name).stem or name
            payload.append({"query": query, "expected_document_ids": [document_id]})
    if not payload:
        raise ValueError("没有可用于生产 RAG 构建的已索引文档")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def active_document_ids() -> set[str]:
    """Return document IDs present in the selected active manifest."""
    try:
        from runtime.rag_production import active_rag_status

        status = active_rag_status()
        manifest = status.get("manifest") if isinstance(status, dict) else {}
        return {
            str(item.get("document_id"))
            for item in (manifest.get("source_documents") or [])
            if isinstance(item, dict) and str(item.get("document_id") or "")
        }
    except Exception:
        return set()


def active_rag_snapshot() -> dict[str, Any]:
    """Return a redacted GUI-facing snapshot of the effective active RAG."""
    try:
        from runtime.rag_production import active_rag_status

        status = active_rag_status()
        manifest = status.get("manifest") if isinstance(status.get("manifest"), dict) else {}
        document_ids = sorted({
            str(item.get("document_id"))
            for item in (manifest.get("source_documents") or [])
            if isinstance(item, dict) and str(item.get("document_id") or "")
        })
        return {
            "active": bool(status.get("active")),
            "reason": str(status.get("reason") or ""),
            "storage_scope": str(status.get("storage_scope") or ""),
            "manifest_path": str(status.get("manifest_path") or ""),
            "document_ids": document_ids,
            "source_documents": [dict(item) for item in (manifest.get("source_documents") or []) if isinstance(item, dict)],
            "semantic": dict(status.get("semantic") or {}),
            "rerank": {
                "enabled": True,
                "stage_version": "rerank.v1",
                "candidate_policy": "max(30, top_k*5)",
            },
        }
    except Exception as exc:
        return {
            "active": False,
            "reason": f"status_error:{type(exc).__name__}",
            "storage_scope": "",
            "manifest_path": "",
            "document_ids": [],
            "source_documents": [],
            "semantic": {},
            "rerank": {"enabled": False, "stage_version": "", "candidate_policy": ""},
        }


def bootstrap_active_catalog(settings: Any, knowledge_base: Any) -> dict[str, Any]:
    """Migrate active manifest documents into an empty/stale GUI catalog without reparsing."""
    snapshot = active_rag_snapshot()
    active_sources = snapshot.get("source_documents") or []
    existing = {str(item.document_id): item for item in knowledge_base.list_documents()}
    folders_by_name = {str(item.name): item for item in knowledge_base.list_folders() if not item.parent_id}
    owner_by_document: dict[str, str] = {}
    for profile_name, profile in settings.generation_profiles.items():
        for document_id in profile.knowledge_base_document_ids:
            owner_by_document.setdefault(str(document_id), str(profile_name))
    imported: list[str] = []
    missing_sources: list[str] = []
    profile_folders: dict[str, str] = {}
    for item in active_sources:
        document_id = str(item.get("document_id") or "")
        if not document_id:
            continue
        if document_id in existing:
            # Repair stale persisted folder IDs from the catalog's current row.
            owner = owner_by_document.get(document_id)
            if owner and existing[document_id].folder_id:
                profile = settings.generation_profiles.get(owner)
                if profile is not None:
                    profile.knowledge_base_folder_ids = [existing[document_id].folder_id]
            continue
        source_path = Path(str(item.get("source_path") or ""))
        if not source_path.is_absolute() and snapshot.get("manifest_path"):
            source_path = Path(str(snapshot["manifest_path"])).resolve().parent / source_path
        if not source_path.is_file():
            missing_sources.append(document_id)
            continue
        owner = owner_by_document.get(document_id, "生产知识库")
        folder = folders_by_name.get(owner)
        if folder is None:
            folder = knowledge_base.create_folder(owner)
            folders_by_name[owner] = folder
        profile_folders[owner] = str(folder.folder_id)
        knowledge_base.register_ready_document(
            document_id=document_id,
            path=source_path,
            sha256=str(item.get("source_sha256") or ""),
            folder_id=folder.folder_id,
        )
        imported.append(document_id)
    for profile_name, folder_id in profile_folders.items():
        profile = settings.generation_profiles.get(profile_name)
        if profile is not None:
            profile.knowledge_base_folder_ids = [folder_id]
    if imported:
        settings.knowledge_base.enabled_document_ids = sorted(set(snapshot.get("document_ids") or []))
    return {"imported_document_ids": sorted(imported), "missing_source_document_ids": sorted(missing_sources)}


def synchronize_settings_scopes(settings: Any, knowledge_base: Any, active_ids: set[str]) -> dict[str, Any]:
    """Make active-v2 IDs authoritative and derive profile IDs from folders."""
    active = {str(item) for item in active_ids if str(item)}
    settings.knowledge_base.enabled_document_ids = sorted(active)
    folders = {str(item.folder_id) for item in knowledge_base.list_folders()}
    profiles: dict[str, dict[str, Any]] = {}
    for name, profile in settings.generation_profiles.items():
        selected_folders = [
            str(item) for item in profile.knowledge_base_folder_ids
            if str(item) and str(item) in folders
        ]
        resolved = knowledge_base.document_ids_for_folders(selected_folders) if selected_folders else []
        effective = sorted({item for item in resolved if item in active})
        stale = sorted(set(resolved) - active)
        profile.knowledge_base_folder_ids = selected_folders
        # Replace, never merge: this removes invisible IDs from older selections.
        profile.knowledge_base_document_ids = effective
        profiles[str(name)] = {
            "folder_ids": selected_folders,
            "document_ids": effective,
            "excluded_not_active": stale,
        }
    return {"active_document_ids": sorted(active), "profiles": profiles}


def resolve_profile_folder_scope(profile: Any, knowledge_base: Any, active_ids: set[str]) -> dict[str, Any]:
    """Resolve the current folder selection and replace the profile's explicit IDs."""
    active = {str(item) for item in active_ids if str(item)}
    existing_folders = {str(item.folder_id) for item in knowledge_base.list_folders()}
    selected = [
        str(item) for item in profile.knowledge_base_folder_ids
        if str(item) and str(item) in existing_folders
    ]
    resolved = knowledge_base.document_ids_for_folders(selected) if selected else []
    effective = sorted({item for item in resolved if item in active})
    profile.knowledge_base_folder_ids = selected
    profile.knowledge_base_document_ids = effective
    return {
        "folder_ids": selected,
        "document_ids": effective,
        "excluded_not_active": sorted(set(resolved) - active),
    }


def rebuild_and_activate(
    kb_root: str | Path,
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Build a fresh candidate from GUI-managed source files and atomically activate it."""
    from scripts.build_production_rag import build

    root = Path(kb_root).resolve()
    legacy_db = root / "knowledge_base.sqlite3"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    project_root = _production_build_root()
    input_dir = root / "sync_manifests" / stamp
    catalog_snapshot = _snapshot_catalog(legacy_db, input_dir / "catalog_snapshot.sqlite3")
    records = _snapshot_ready_records(catalog_snapshot)
    if not records:
        from runtime.rag_production import deactivate_rag

        deactivate_rag(reason="catalog_empty")
        if progress:
            progress("知识库已停用：当前目录没有可用文档")
        return {"status": "disabled", "disabled": True, "reason": "catalog_empty", "active_document_ids": []}
    source_dir = root / "source_files"
    if not source_dir.is_dir():
        raise FileNotFoundError(f"知识库源文件目录不存在：{source_dir}")
    output_dir = project_root / f"production-rag-gui-{stamp}"
    query_expectations = input_dir / "gui_query_expectations.json"
    scope_expectations = input_dir / "gui_scope_expectations.json"
    _write_expectations(query_expectations, records)
    _write_expectations(scope_expectations, records, scopes=True)
    if progress:
        progress(f"正在构建生产 RAG 候选及语义索引：{len(records)} 个文档")
    result = build(
        source_dir,
        output_dir,
        activate=True,
        query_expectations_path=query_expectations,
        legacy_db=catalog_snapshot,
        scope_expectations_path=scope_expectations,
        require_v2_scope=True,
        remote_blank_review=True,
        include_names={str(item.name) for item in records},
        conversion_cache_dirs=[
            item / "converted"
            for item in project_root.glob("production-rag-*")
            if (item / "converted").is_dir()
        ],
    )
    activation = result.get("activation_result") if isinstance(result, dict) else None
    if not isinstance(activation, dict) or activation.get("activated") is not True:
        raise RuntimeError(f"生产 RAG 候选未激活：{activation or result.get('status')}")
    if progress:
        semantic = active_rag_snapshot().get("semantic") or {}
        semantic_manifest = semantic.get("manifest") or {}
        progress(
            f"生产 RAG 已激活：{output_dir.name}；语义向量 "
            f"{semantic.get('retrieval_count', 0)} 条（{semantic_manifest.get('model_name') or 'unknown'}）"
        )
    result["output_dir"] = str(output_dir)
    result["active_document_ids"] = sorted(active_document_ids())
    return result
