"""Readable, index-backed citations for knowledge-base answers."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any


_MARKER = re.compile(r"\[KB:[^\]\r\n]+\]")
_PAGE = re.compile(r":page:(\d+)\]$")


def _indexed_metadata(refs: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, list[list[str]]]]:
    """Read only the active, validated catalog; never use a legacy index."""
    from runtime.rag_production import active_rag_status

    status = active_rag_status()
    if not status.get("active"):
        return {}, {}
    path = status.get("database", {}).get("path")
    if not path:
        return {}, {}
    ids = sorted({str(ref.get("document_id") or "") for ref in refs if ref.get("document_id")})
    markers = sorted({str(ref.get("source_ref") or "") for ref in refs if ref.get("source_ref")})
    titles: dict[str, str] = {}
    headings: dict[str, list[list[str]]] = {}
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        if ids:
            slots = ",".join("?" for _ in ids)
            for document_id, source_name in db.execute(
                f"SELECT document_id, source_name FROM pipeline_documents WHERE document_id IN ({slots})", ids
            ):
                if source_name:
                    titles[str(document_id)] = PureWindowsPath(str(source_name)).name
        if markers:
            slots = ",".join("?" for _ in markers)
            for marker, heading in db.execute(
                f"SELECT source_marker, heading_path_json FROM pipeline_chunks "
                f"WHERE retrieval_role='retrieval' AND source_marker IN ({slots})", markers
            ):
                try:
                    path_parts = json.loads(heading or "[]")
                except (ValueError, TypeError):
                    path_parts = []
                headings.setdefault(str(marker), []).append(path_parts if isinstance(path_parts, list) else [])
    return titles, headings


def resolve_knowledge_references(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich a turn or legacy manifest without changing its audit markers."""
    kb_refs = [ref for ref in refs if ref.get("source_type") == "knowledge_base"]
    if not kb_refs:
        return refs
    try:
        titles, indexed_headings = _indexed_metadata(kb_refs)
    except (OSError, sqlite3.Error, ValueError, KeyError):
        titles, indexed_headings = {}, {}
    resolved = []
    for ref in refs:
        item = dict(ref)
        if item.get("source_type") == "knowledge_base":
            marker = str(item.get("source_ref") or "")
            item.setdefault("source_name", titles.get(str(item.get("document_id") or ""), ""))
            if not item.get("heading_path") and marker in indexed_headings:
                item["heading_path"] = indexed_headings[marker]
        resolved.append(item)
    return resolved


def _common_heading(paths: list[list[str]]) -> list[str]:
    if not paths:
        return []
    normalized = [[str(part).strip() for part in path if str(part).strip()] for path in paths]
    common = normalized[0]
    for path in normalized[1:]:
        shared = []
        for a, b in zip(common, path):
            if a != b:
                break
            shared.append(a)
        common = shared
        if not common:
            break
    return common


def render_knowledge_citations(text: str, refs: list[dict[str, Any]], *, streaming: bool = False) -> str:
    by_marker: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        if ref.get("source_type") == "knowledge_base" and ref.get("source_ref"):
            by_marker.setdefault(str(ref["source_ref"]), []).append(ref)

    def replace(match: re.Match[str]) -> str:
        marker = match.group(0)
        matches = by_marker.get(marker, [])
        names = {PureWindowsPath(str(ref.get("source_name") or "")).name for ref in matches if ref.get("source_name")}
        if len(names) != 1:
            return "（知识库来源未核实）"
        name = names.pop()
        paths: list[list[str]] = []
        for ref in matches:
            heading = ref.get("heading_path") or []
            if heading and isinstance(heading, list) and isinstance(heading[0], list):
                paths.extend(heading)
            else:
                paths.append(heading if isinstance(heading, list) else [])
        heading_text = "，".join(_common_heading(paths))
        page = _PAGE.search(marker)
        parts = [f"《{name}》"]
        if heading_text:
            parts.append(heading_text)
        if page:
            parts.append(f"第{int(page.group(1))}页")
        return "（" + "，".join(parts) + "）"

    rendered = _MARKER.sub(replace, str(text))
    if streaming:
        rendered = re.sub(r"\[K(?:B(?::[^\]\r\n]*)?)?$", "", rendered)
    return rendered
