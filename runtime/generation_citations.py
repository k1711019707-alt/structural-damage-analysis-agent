"""Deterministic, persisted citations for generated engineering documents."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any


_KB_MARKER = re.compile(r"(?<!内部标识：)(?<!內部標識：)\[KB:([^:\]\r\n]+):([^\]\r\n]+)\]")


def _source_basename(value: Any) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    return PurePosixPath(normalized).name if normalized else ""


def _metadata_source_name(metadata: Mapping[str, Any]) -> str:
    for value in (metadata.get("source_name"), metadata.get("source_path")):
        name = _source_basename(value)
        if name:
            return name
    canonical = metadata.get("canonical_source")
    if isinstance(canonical, Mapping):
        for value in (canonical.get("source_name"), canonical.get("source_path")):
            name = _source_basename(value)
            if name:
                return name
    return ""


def _active_index_source_names(document_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted({str(value).strip() for value in document_ids if str(value).strip()})
    if not ids:
        return {}
    try:
        from runtime.rag_production import active_rag_status

        status = active_rag_status()
        database = status.get("database") if isinstance(status, dict) else None
        database_path = database.get("path") if status.get("active") and isinstance(database, dict) else ""
        if not database_path:
            return {}
        placeholders = ",".join("?" for _ in ids)
        uri = Path(str(database_path)).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                f"SELECT document_id, source_name FROM pipeline_documents "
                f"WHERE document_id IN ({placeholders})",
                ids,
            ).fetchall()
    except (ImportError, OSError, sqlite3.Error, TypeError, ValueError, KeyError):
        return {}
    return {
        str(document_id): name
        for document_id, source_name in rows
        if (name := _source_basename(source_name))
    }


def normalize_citation_catalog(catalog: Iterable[Mapping[str, Any]] | None) -> list[dict[str, str]]:
    """Validate persisted catalog entries and remove paths or ambiguous markers."""
    by_marker: dict[str, dict[str, str]] = {}
    conflicted: set[str] = set()
    for raw in catalog or ():
        if not isinstance(raw, Mapping):
            continue
        marker = str(raw.get("source_marker") or "").strip()
        match = _KB_MARKER.fullmatch(marker)
        document_id = str(raw.get("document_id") or "").strip()
        source_name = _source_basename(raw.get("source_name"))
        if not match or match.group(1) != document_id or not source_name:
            continue
        entry = {
            "source_marker": marker,
            "document_id": document_id,
            "source_name": source_name,
            "location": match.group(2),
        }
        existing = by_marker.get(marker)
        if existing is not None and existing != entry:
            conflicted.add(marker)
            by_marker.pop(marker, None)
        elif marker not in conflicted:
            by_marker[marker] = entry
    return [by_marker[marker] for marker in sorted(by_marker)]


def build_citation_catalog(
    retrieved_chunks: Iterable[Mapping[str, Any]],
    *,
    indexed_source_names: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    """Freeze authoritative source display metadata for the retrieved markers."""
    chunks = [dict(chunk) for chunk in retrieved_chunks if isinstance(chunk, Mapping)]
    document_ids = [str(chunk.get("document_id") or "").strip() for chunk in chunks]
    index_names = (
        {str(key): _source_basename(value) for key, value in indexed_source_names.items()}
        if indexed_source_names is not None
        else _active_index_source_names(document_ids)
    )
    entries: list[dict[str, str]] = []
    for chunk in chunks:
        marker = str(chunk.get("source_marker") or "").strip()
        match = _KB_MARKER.fullmatch(marker)
        document_id = str(chunk.get("document_id") or "").strip()
        if not match or match.group(1) != document_id:
            continue
        metadata = chunk.get("metadata")
        source_name = _metadata_source_name(metadata if isinstance(metadata, Mapping) else {})
        source_name = source_name or index_names.get(document_id, "")
        if not source_name:
            continue
        entries.append({
            "source_marker": marker,
            "document_id": document_id,
            "source_name": source_name,
            "location": match.group(2),
        })
    return normalize_citation_catalog(entries)


def render_knowledge_citations(
    text: Any,
    catalog: Iterable[Mapping[str, Any]] | None,
    *,
    traditional: bool = False,
) -> str:
    """Expand exact KB markers without hiding their stable audit identity."""
    by_marker = {
        entry["source_marker"]: entry
        for entry in normalize_citation_catalog(catalog)
    }
    source_label = "來源" if traditional else "来源"
    unresolved_label = "來源未解析" if traditional else "来源未解析"
    identity_label = "內部標識" if traditional else "内部标识"
    page_suffix = "頁" if traditional else "页"
    location_label = "位置"

    def replace(match: re.Match[str]) -> str:
        marker = match.group(0)
        entry = by_marker.get(marker)
        if entry is None:
            return f"（{unresolved_label}；{identity_label}：{marker}）"
        location = entry.get("location", "")
        page = re.fullmatch(r"page:(\d+)", location, flags=re.IGNORECASE)
        location_text = f"第{int(page.group(1))}{page_suffix}" if page else f"{location_label}：{location}"
        return (
            f"（{source_label}：{entry['source_name']}，{location_text}；"
            f"{identity_label}：{marker}）"
        )

    return _KB_MARKER.sub(replace, str(text))
