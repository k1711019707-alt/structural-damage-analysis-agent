"""SQLite/FTS5 indexing stage for knowledge-chunks.v2."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import INDEX_SCHEMA_VERSION, read_json, write_json

_STANDARD_RE = re.compile(r"\b(?:GB|JGJ|CECS|DBJ|CJJ|DL|TB|JG)\s*[-—]?\s*\d{2,6}(?:\s*[-—]\s*\d{4})?\b", re.I)
_CLAUSE_RE = re.compile(r"(?:第\s*)?([0-9]+(?:\.[0-9]+){1,5})(?:\s*条)?")
INDEX_STAGE_VERSION = "index.v2.2"


def _clean(value: Any) -> str:
    text = str(value or "").replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t\n]+", " ", text).strip()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fields(item: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    text_search = _clean(metadata.get("text_search") or item.get("text") or "")
    heading = metadata.get("heading_path") or []
    if isinstance(heading, str):
        heading = [heading]
    heading = [_clean(x) for x in heading if _clean(x)]
    role = _clean(metadata.get("retrieval_role") or ("context_only" if metadata.get("chunk_level") == "parent" else "retrieval"))
    level = _clean(metadata.get("chunk_level") or ("parent" if role == "context_only" else "child"))
    pages = metadata.get("page_numbers") or []
    if not isinstance(pages, list): pages = [pages]
    pages = [int(p) for p in pages if str(p).isdigit() and int(p) > 0]
    flags = metadata.get("quality_flags") or []
    if isinstance(flags, str): flags = [flags]
    clause = _clean(metadata.get("clause_number"))
    if clause.startswith("第"): clause = clause[1:]
    if clause.endswith("条"): clause = clause[:-1]
    standard_match = _STANDARD_RE.search(" ".join((text_search, " > ".join(heading), _clean(item.get("source_marker")))))
    standard = re.sub(r"\s+", "", standard_match.group(0)).replace("—", "-") if standard_match else ""
    digest = hashlib.sha1(text_search.casefold().encode("utf-8")).hexdigest()
    kind = _clean(metadata.get("chunk_type") or "paragraph")
    region_id = _clean(metadata.get("region_id"))
    model_generated = bool(metadata.get("model_generated"))
    return {
        "metadata": metadata, "text_search": text_search,
        "text_raw": _clean(metadata.get("text_raw") or text_search),
        "text_contextualized": _clean(metadata.get("text_contextualized") or item.get("text") or text_search),
        "role": role, "level": level, "kind": kind, "clause": clause,
        "heading": heading, "heading_text": " > ".join(heading), "standard": standard,
        "pages": pages, "primary_page": pages[0] if pages else None,
        "methods": metadata.get("extraction_methods") or [], "flags": flags,
        "cross_page": int(bool(metadata.get("is_cross_page"))), "part": int(metadata.get("part") or 0),
        "tokens": int(metadata.get("token_count") or 0), "chars": int(metadata.get("char_count") or len(text_search)),
        "hash": digest, "table_id": _clean(metadata.get("table_id")), "image_id": _clean(metadata.get("image_id")),
        "region_id": region_id, "region_type": _clean(metadata.get("region_type")),
        "table_source": _clean(metadata.get("table_source")),
        "needs_review": int(bool(metadata.get("needs_review") or model_generated or "needs_review" in flags)),
        "model_generated": int(model_generated),
        "table_text": text_search if kind in {"table", "table_row"} else "",
        "image_text": text_search if kind == "image_ocr" else "",
    }


def _schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS pipeline_documents (
      document_id TEXT PRIMARY KEY, source_path TEXT NOT NULL DEFAULT '', source_name TEXT NOT NULL DEFAULT '', source_sha256 TEXT NOT NULL DEFAULT '',
      schema_version TEXT NOT NULL DEFAULT '', index_version TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ready', quality_score REAL,
      page_count INTEGER NOT NULL DEFAULT 0, chunk_count INTEGER NOT NULL DEFAULT 0, index_fingerprint TEXT NOT NULL DEFAULT '',
      retrieval_corpus_fingerprint TEXT NOT NULL DEFAULT '', retrieval_chunk_count INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS pipeline_pages (
      document_id TEXT NOT NULL, page_number INTEGER NOT NULL, extraction_methods_json TEXT NOT NULL DEFAULT '[]', chunk_count INTEGER NOT NULL DEFAULT 0,
      needs_review INTEGER NOT NULL DEFAULT 0, warnings_json TEXT NOT NULL DEFAULT '[]', PRIMARY KEY(document_id,page_number)
    );
    CREATE TABLE IF NOT EXISTS pipeline_chunks (
      chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, location TEXT NOT NULL, text TEXT NOT NULL, text_search TEXT NOT NULL DEFAULT '',
      text_raw TEXT NOT NULL DEFAULT '', text_contextualized TEXT NOT NULL DEFAULT '', parent_id TEXT NOT NULL DEFAULT '', source_marker TEXT NOT NULL DEFAULT '',
      chunk_level TEXT NOT NULL DEFAULT 'child', retrieval_role TEXT NOT NULL DEFAULT 'retrieval', chunk_type TEXT NOT NULL DEFAULT 'paragraph', clause_number TEXT NOT NULL DEFAULT '',
      heading_path_json TEXT NOT NULL DEFAULT '[]', page_numbers_json TEXT NOT NULL DEFAULT '[]', primary_page INTEGER, extraction_methods_json TEXT NOT NULL DEFAULT '[]',
      quality_flags_json TEXT NOT NULL DEFAULT '[]', is_cross_page INTEGER NOT NULL DEFAULT 0, part INTEGER NOT NULL DEFAULT 0, token_count INTEGER NOT NULL DEFAULT 0,
      char_count INTEGER NOT NULL DEFAULT 0, content_hash TEXT NOT NULL DEFAULT '', duplicate_group_id TEXT NOT NULL DEFAULT '', canonical_chunk_id TEXT NOT NULL DEFAULT '',
      standard_number TEXT NOT NULL DEFAULT '', table_id TEXT NOT NULL DEFAULT '', image_id TEXT NOT NULL DEFAULT '', table_source TEXT NOT NULL DEFAULT '', needs_review INTEGER NOT NULL DEFAULT 0,
      metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_pipeline_chunks_role ON pipeline_chunks(document_id,retrieval_role);
    CREATE INDEX IF NOT EXISTS idx_pipeline_chunks_clause ON pipeline_chunks(clause_number);
    CREATE INDEX IF NOT EXISTS idx_pipeline_chunks_standard ON pipeline_chunks(standard_number);
    CREATE INDEX IF NOT EXISTS idx_pipeline_chunks_type ON pipeline_chunks(chunk_type);
    CREATE TABLE IF NOT EXISTS pipeline_chunk_relations(parent_id TEXT NOT NULL, child_id TEXT NOT NULL, relation_type TEXT NOT NULL DEFAULT 'parent_child', child_order INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(parent_id,child_id));
    CREATE TABLE IF NOT EXISTS pipeline_chunk_pages(chunk_id TEXT NOT NULL,page_number INTEGER NOT NULL,PRIMARY KEY(chunk_id,page_number));
    CREATE TABLE IF NOT EXISTS pipeline_chunk_headings(chunk_id TEXT NOT NULL,heading_level INTEGER NOT NULL,heading_text TEXT NOT NULL,heading_order INTEGER NOT NULL,PRIMARY KEY(chunk_id,heading_order));
    CREATE TABLE IF NOT EXISTS pipeline_chunk_quality(chunk_id TEXT NOT NULL,flag TEXT NOT NULL,severity TEXT NOT NULL DEFAULT 'warning',PRIMARY KEY(chunk_id,flag));
    CREATE TABLE IF NOT EXISTS pipeline_tables(table_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,chunk_id TEXT NOT NULL DEFAULT '',page_number INTEGER,source TEXT NOT NULL DEFAULT '',needs_review INTEGER NOT NULL DEFAULT 0,metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS pipeline_images(image_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,chunk_id TEXT NOT NULL DEFAULT '',page_number INTEGER,asset_path TEXT NOT NULL DEFAULT '',image_hash TEXT NOT NULL DEFAULT '',ocr_text TEXT NOT NULL DEFAULT '',needs_review INTEGER NOT NULL DEFAULT 0,metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS pipeline_visual_regions (
      document_id TEXT NOT NULL, region_id TEXT NOT NULL, chunk_id TEXT NOT NULL DEFAULT '', page_number INTEGER,
      bbox_json TEXT NOT NULL DEFAULT 'null', region_type TEXT NOT NULL DEFAULT 'visual', ocr_text TEXT NOT NULL DEFAULT '',
      vision_search TEXT NOT NULL DEFAULT '', vision_summary TEXT NOT NULL DEFAULT '', vision_raw TEXT NOT NULL DEFAULT '',
      model_generated INTEGER NOT NULL DEFAULT 0, needs_review INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}',
      PRIMARY KEY(document_id,region_id)
    );
    CREATE TABLE IF NOT EXISTS pipeline_evidence_links (
      document_id TEXT NOT NULL, evidence_type TEXT NOT NULL, evidence_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
      chunk_level TEXT NOT NULL DEFAULT 'child', retrieval_role TEXT NOT NULL DEFAULT 'retrieval', child_order INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(document_id,evidence_type,evidence_id,chunk_id)
    );
    CREATE INDEX IF NOT EXISTS idx_pipeline_evidence_links_chunk ON pipeline_evidence_links(document_id,chunk_id);
    CREATE INDEX IF NOT EXISTS idx_pipeline_visual_regions_page ON pipeline_visual_regions(document_id,page_number);
    CREATE TABLE IF NOT EXISTS pipeline_index_manifest(document_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL);
    """)
    def ensure_columns(table: str, columns: dict[str, str]) -> None:
        existing = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    ensure_columns("pipeline_documents", {
        "source_path": "TEXT NOT NULL DEFAULT ''", "source_name": "TEXT NOT NULL DEFAULT ''", "source_sha256": "TEXT NOT NULL DEFAULT ''",
        "schema_version": "TEXT NOT NULL DEFAULT ''", "index_version": "TEXT NOT NULL DEFAULT ''", "quality_score": "REAL", "page_count": "INTEGER NOT NULL DEFAULT 0",
        "chunk_count": "INTEGER NOT NULL DEFAULT 0", "index_fingerprint": "TEXT NOT NULL DEFAULT ''", "retrieval_corpus_fingerprint": "TEXT NOT NULL DEFAULT ''", "retrieval_chunk_count": "INTEGER NOT NULL DEFAULT 0", "metadata_json": "TEXT NOT NULL DEFAULT '{}'", "created_at": "TEXT NOT NULL DEFAULT ''", "updated_at": "TEXT NOT NULL DEFAULT ''",
    })
    ensure_columns("pipeline_chunks", {
        "text_search": "TEXT NOT NULL DEFAULT ''", "text_raw": "TEXT NOT NULL DEFAULT ''", "text_contextualized": "TEXT NOT NULL DEFAULT ''", "parent_id": "TEXT NOT NULL DEFAULT ''", "source_marker": "TEXT NOT NULL DEFAULT ''",
        "chunk_level": "TEXT NOT NULL DEFAULT 'child'", "retrieval_role": "TEXT NOT NULL DEFAULT 'retrieval'", "chunk_type": "TEXT NOT NULL DEFAULT 'paragraph'", "clause_number": "TEXT NOT NULL DEFAULT ''", "heading_path_json": "TEXT NOT NULL DEFAULT '[]'", "page_numbers_json": "TEXT NOT NULL DEFAULT '[]'", "primary_page": "INTEGER", "extraction_methods_json": "TEXT NOT NULL DEFAULT '[]'", "quality_flags_json": "TEXT NOT NULL DEFAULT '[]'", "is_cross_page": "INTEGER NOT NULL DEFAULT 0", "part": "INTEGER NOT NULL DEFAULT 0", "token_count": "INTEGER NOT NULL DEFAULT 0", "char_count": "INTEGER NOT NULL DEFAULT 0", "content_hash": "TEXT NOT NULL DEFAULT ''", "duplicate_group_id": "TEXT NOT NULL DEFAULT ''", "canonical_chunk_id": "TEXT NOT NULL DEFAULT ''", "standard_number": "TEXT NOT NULL DEFAULT ''", "table_id": "TEXT NOT NULL DEFAULT ''", "image_id": "TEXT NOT NULL DEFAULT ''", "table_source": "TEXT NOT NULL DEFAULT ''", "needs_review": "INTEGER NOT NULL DEFAULT 0", "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    })
    db.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_chunks_retrieval_order ON pipeline_chunks(document_id,retrieval_role,primary_page,location,part,chunk_id)")
    current = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='pipeline_chunks_fts'").fetchone()
    if current and "text_search" not in (current[0] or ""):
        db.execute("DROP TABLE pipeline_chunks_fts")
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS pipeline_chunks_fts USING fts5(
      chunk_id UNINDEXED, document_id UNINDEXED, location UNINDEXED,
      text_search, heading_text, clause_number, standard_number, table_text, image_text
    )""")


def _fingerprint(payload: dict[str, Any], chunks: list[dict[str, Any]]) -> str:
    value = {"index_stage_version": INDEX_STAGE_VERSION, "source_sha256": payload.get("source_sha256", ""), "schema_version": payload.get("schema_version", ""), "chunk_config": payload.get("chunk_config") or {}, "quality_report": payload.get("quality_report") or {}, "page_inventory": payload.get("page_inventory") or [], "conversion_warnings": payload.get("conversion_warnings") or [], "image_references": payload.get("image_references") or [], "table_diagnostics": payload.get("table_diagnostics") or [], "chunks": [(x.get("chunk_id"), _clean(x.get("text")), x.get("metadata") or {}) for x in chunks]}
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _retrieval_corpus_fingerprint(chunks: list[dict[str, Any]], prepared: list[dict[str, Any]]) -> str:
    """Fingerprint the exact child corpus shared by BM25 and embeddings."""
    rows = [
        (str(item.get("location", "")), str(item.get("chunk_id", "")), fields["hash"])
        for item, fields in zip(chunks, prepared)
        if fields["role"] == "retrieval" and fields["text_search"]
    ]
    rows.sort(key=lambda value: (value[0], value[1]))
    identity = [(chunk_id, content_hash) for _, chunk_id, content_hash in rows]
    return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()


def build_index(chunks_payload: dict[str, Any], db_path: str | Path) -> dict[str, Any]:
    target = Path(db_path); target.parent.mkdir(parents=True, exist_ok=True)
    chunks = [x for x in (chunks_payload.get("chunks") or []) if isinstance(x, dict)]
    image_references = [x for x in (chunks_payload.get("image_references") or []) if isinstance(x, dict)]
    table_diagnostics = [x for x in (chunks_payload.get("table_diagnostics") or []) if isinstance(x, dict)]
    page_inventory = [x for x in (chunks_payload.get("page_inventory") or []) if isinstance(x, dict)]
    quality_report = dict(chunks_payload.get("quality_report") or {})
    document_id = str(chunks_payload.get("document_id", ""))
    if not document_id: raise ValueError("chunks payload 缺少 document_id")
    fingerprint = _fingerprint(chunks_payload, chunks); source_sha256 = str(chunks_payload.get("source_sha256", "")); now = _now()
    with closing(sqlite3.connect(target)) as db:
        db.row_factory = sqlite3.Row; db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=NORMAL"); db.execute("PRAGMA temp_store=MEMORY"); db.execute("PRAGMA foreign_keys=ON")
        with db:
            _schema(db)
            previous = db.execute("SELECT * FROM pipeline_documents WHERE document_id=?", (document_id,)).fetchone()
            if previous and previous["index_fingerprint"] == fingerprint:
                row = db.execute("SELECT payload_json FROM pipeline_index_manifest WHERE document_id=?", (document_id,)).fetchone()
                result = json.loads(row[0]) if row else {"document_id": document_id, "chunk_count": len(chunks)}
                result.update({"status": "unchanged", "skipped": True, "db_path": str(target.resolve())}); return result
            old_ids = [r[0] for r in db.execute("SELECT chunk_id FROM pipeline_chunks WHERE document_id=?", (document_id,))]
            if old_ids: db.executemany("DELETE FROM pipeline_chunks_fts WHERE chunk_id=?", [(x,) for x in old_ids])
            db.execute("DELETE FROM pipeline_chunk_relations WHERE parent_id IN (SELECT chunk_id FROM pipeline_chunks WHERE document_id=?) OR child_id IN (SELECT chunk_id FROM pipeline_chunks WHERE document_id=?)", (document_id, document_id))
            for table in ("pipeline_chunk_pages", "pipeline_chunk_headings", "pipeline_chunk_quality"):
                db.execute(f"DELETE FROM {table} WHERE chunk_id IN (SELECT chunk_id FROM pipeline_chunks WHERE document_id=?)", (document_id,))
            db.execute("DELETE FROM pipeline_tables WHERE document_id=?", (document_id,)); db.execute("DELETE FROM pipeline_images WHERE document_id=?", (document_id,)); db.execute("DELETE FROM pipeline_visual_regions WHERE document_id=?", (document_id,)); db.execute("DELETE FROM pipeline_evidence_links WHERE document_id=?", (document_id,)); db.execute("DELETE FROM pipeline_pages WHERE document_id=?", (document_id,)); db.execute("DELETE FROM pipeline_chunks WHERE document_id=?", (document_id,))
            prepared = [_fields(x) for x in chunks]; retrieval_corpus_fingerprint = _retrieval_corpus_fingerprint(chunks, prepared); canonical: dict[str, str] = {}; rows = []
            for item, f in zip(chunks, prepared):
                cid = str(item.get("chunk_id", "")); group = f["hash"]; canon = canonical.setdefault(group, cid)
                rows.append((cid, document_id, str(item.get("location", "")), str(item.get("text", "")), f["text_search"], f["text_raw"], f["text_contextualized"], str(item.get("parent_id", "")), str(item.get("source_marker", "")), f["level"], f["role"], f["kind"], f["clause"], _json(f["heading"]), _json(f["pages"]), f["primary_page"], _json(f["methods"]), _json(f["flags"]), f["cross_page"], f["part"], f["tokens"], f["chars"], f["hash"], group, canon, f["standard"], f["table_id"], f["image_id"], f["table_source"], f["needs_review"], _json(f["metadata"])))
            chunk_columns = "chunk_id,document_id,location,text,text_search,text_raw,text_contextualized,parent_id,source_marker,chunk_level,retrieval_role,chunk_type,clause_number,heading_path_json,page_numbers_json,primary_page,extraction_methods_json,quality_flags_json,is_cross_page,part,token_count,char_count,content_hash,duplicate_group_id,canonical_chunk_id,standard_number,table_id,image_id,table_source,needs_review,metadata_json"
            db.executemany(f"INSERT INTO pipeline_chunks ({chunk_columns}) VALUES ({','.join('?' for _ in range(31))})", rows)
            db.executemany("INSERT INTO pipeline_chunks_fts VALUES (?,?,?,?,?,?,?,?,?)", [(str(x.get("chunk_id", "")), document_id, str(x.get("location", "")), f["text_search"], f["heading_text"], f["clause"], f["standard"], f["table_text"], f["image_text"]) for x, f in zip(chunks, prepared) if f["role"] == "retrieval" and f["text_search"]])
            chunk_ids = {str(item.get("chunk_id", "")) for item in chunks}
            evidence_anchors: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
            for item, f in zip(chunks, prepared):
                cid, parent = str(item.get("chunk_id", "")), str(item.get("parent_id", ""))
                db.executemany("INSERT OR IGNORE INTO pipeline_chunk_pages VALUES (?,?)", [(cid, p) for p in f["pages"]]); db.executemany("INSERT OR IGNORE INTO pipeline_chunk_headings VALUES (?,?,?,?)", [(cid, i, h, i) for i, h in enumerate(f["heading"])]); db.executemany("INSERT OR IGNORE INTO pipeline_chunk_quality VALUES (?,?,?)", [(cid, str(flag), "error" if "error" in str(flag) else "warning") for flag in f["flags"]])
                if f["level"] == "child" and parent: db.execute("INSERT OR IGNORE INTO pipeline_chunk_relations VALUES (?,?,?,?)", (parent, cid, "parent_child", f["part"]))
                for evidence_type, evidence_id in (("table", f["table_id"]), ("image", f["image_id"]), ("visual_region", f["region_id"])):
                    if not evidence_id:
                        continue
                    db.execute("INSERT OR IGNORE INTO pipeline_evidence_links VALUES (?,?,?,?,?,?,?)", (document_id, evidence_type, evidence_id, cid, f["level"], f["role"], f["part"]))
                    anchor_id = parent if parent in chunk_ids else cid
                    key = (evidence_type, evidence_id)
                    if key not in evidence_anchors or f["level"] == "parent":
                        evidence_anchors[key] = (anchor_id, f)
            for (evidence_type, evidence_id), (anchor_id, f) in evidence_anchors.items():
                if evidence_type == "table":
                    db.execute("INSERT OR REPLACE INTO pipeline_tables VALUES (?,?,?,?,?,?,?)", (evidence_id, document_id, anchor_id, f["primary_page"], f["table_source"], f["needs_review"], _json(f["metadata"])))
                elif evidence_type == "image":
                    db.execute("INSERT OR REPLACE INTO pipeline_images VALUES (?,?,?,?,?,?,?,?,?)", (evidence_id, document_id, anchor_id, f["primary_page"], str(f["metadata"].get("asset_path", "")), str(f["metadata"].get("image_hash", "")), str(f["metadata"].get("ocr_text") or f["text_raw"]), f["needs_review"], _json(f["metadata"])))
                else:
                    metadata = f["metadata"]
                    db.execute("INSERT OR REPLACE INTO pipeline_visual_regions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (document_id, evidence_id, anchor_id, f["primary_page"], _json(metadata.get("bbox")), f["region_type"] or "visual", _clean(metadata.get("ocr_text")), _clean(metadata.get("vision_search")), _clean(metadata.get("vision_summary")), str(metadata.get("vision_raw") or ""), f["model_generated"], f["needs_review"], _json(metadata)))
            for diagnostic in table_diagnostics:
                table_id = _clean(diagnostic.get("table_id"))
                if table_id:
                    db.execute("INSERT OR IGNORE INTO pipeline_tables VALUES (?,?,?,?,?,?,?)", (table_id, document_id, "", diagnostic.get("page_number"), _clean(diagnostic.get("extraction_method")), int(bool(diagnostic.get("needs_review", True))), _json(diagnostic)))
            for reference in image_references:
                region_id = _clean(reference.get("region_id"))
                image_id = _clean(reference.get("image_id"))
                if region_id:
                    db.execute("INSERT OR IGNORE INTO pipeline_visual_regions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (document_id, region_id, "", reference.get("page_number"), _json(reference.get("bbox")), _clean(reference.get("region_type")) or "visual", "", "", "", "", 0, int(bool(reference.get("needs_review", True))), _json(reference)))
                elif image_id:
                    db.execute("INSERT OR IGNORE INTO pipeline_images VALUES (?,?,?,?,?,?,?,?,?)", (image_id, document_id, "", reference.get("page_number"), str(reference.get("asset_path") or ""), str(reference.get("image_hash") or ""), "", int(bool(reference.get("needs_review", True))), _json(reference)))
            page_stats: dict[int, dict[str, Any]] = {}
            failed_pages = {int(page) for page in (quality_report.get("failed_pages") or []) if str(page).isdigit() and int(page) > 0}
            low_quality_pages = {int(page) for page in (quality_report.get("low_quality_pages") or []) if str(page).isdigit() and int(page) > 0}
            for page_record in page_inventory:
                page = page_record.get("page_number")
                if not str(page).isdigit() or int(page) <= 0:
                    continue
                page_number = int(page)
                method = _clean(page_record.get("extraction_method"))
                page_warnings = {str(item) for item in (page_record.get("warnings") or []) if str(item).strip()}
                if page_number in failed_pages:
                    page_warnings.add("failed_page")
                if page_number in low_quality_pages:
                    page_warnings.add("low_quality_page")
                page_stats[page_number] = {
                    "methods": {method} if method else set(),
                    "count": 0,
                    "review": int(bool(page_record.get("needs_review") or page_number in failed_pages or page_number in low_quality_pages)),
                    "warnings": page_warnings,
                }
            for f in prepared:
                for p in f["pages"]:
                    s = page_stats.setdefault(p, {"methods": set(), "count": 0, "review": 0, "warnings": set()}); s["methods"].update(map(str, f["methods"])); s["count"] += 1; s["review"] |= f["needs_review"]; s["warnings"].update(map(str, f["flags"]))
            for evidence in [*table_diagnostics, *image_references]:
                page = evidence.get("page_number")
                if not str(page).isdigit() or int(page) <= 0:
                    continue
                s = page_stats.setdefault(int(page), {"methods": set(), "count": 0, "review": 0, "warnings": set()})
                if evidence.get("extraction_method"):
                    s["methods"].add(str(evidence["extraction_method"]))
                s["review"] |= int(bool(evidence.get("needs_review", True)))
                s["warnings"].update(map(str, evidence.get("quality_flags") or []))
            db.executemany("INSERT INTO pipeline_pages VALUES (?,?,?,?,?,?)", [(document_id, p, _json(sorted(s["methods"])), s["count"], s["review"], _json(sorted(s["warnings"]))) for p, s in page_stats.items()])
            duplicate_count = len(prepared) - len(canonical); parent_count = sum(f["level"] == "parent" for f in prepared); quality_count = sum(len(f["flags"]) for f in prepared)
            table_ids = {f["table_id"] for f in prepared if f["table_id"]} | {_clean(x.get("table_id")) for x in table_diagnostics if _clean(x.get("table_id"))}
            image_ids = {f["image_id"] for f in prepared if f["image_id"]} | {_clean(x.get("image_id")) for x in image_references if _clean(x.get("image_id"))}
            region_ids = {f["region_id"] for f in prepared if f["region_id"]} | {_clean(x.get("region_id")) for x in image_references if _clean(x.get("region_id"))}
            retrieval_count = sum(f["role"] == "retrieval" and bool(f["text_search"]) for f in prepared)
            report = {"schema_version": INDEX_SCHEMA_VERSION, "stage_version": INDEX_STAGE_VERSION, "db_path": str(target.resolve()), "document_id": document_id, "source_sha256": source_sha256, "chunk_schema_version": chunks_payload.get("schema_version", ""), "chunk_count": len(chunks), "parent_count": parent_count, "child_count": len(chunks) - parent_count, "retrieval_chunk_count": retrieval_count, "retrieval_corpus_fingerprint": retrieval_corpus_fingerprint, "retrieval_fts_columns": ["text_search", "heading_text", "clause_number", "standard_number", "table_text", "image_text"], "page_count": len(page_stats), "failed_page_count": len(failed_pages), "low_quality_page_count": len(low_quality_pages), "quality_score": quality_report.get("quality_score"), "table_chunk_count": sum(bool(f["table_id"]) for f in prepared), "table_count": len(table_ids), "table_diagnostic_count": len(table_diagnostics), "image_ocr_chunk_count": sum(bool(f["image_id"]) for f in prepared), "image_count": len(image_ids), "image_reference_count": sum(bool(_clean(x.get("image_id"))) for x in image_references), "visual_region_chunk_count": sum(bool(f["region_id"]) for f in prepared), "visual_region_count": len(region_ids), "visual_reference_count": sum(bool(_clean(x.get("region_id"))) for x in image_references), "duplicate_count": duplicate_count, "quality_flag_count": quality_count, "status": "ready", "skipped": False}
            meta = dict(chunks_payload.get("metadata") or {})
            source_path = str(chunks_payload.get("source_path") or meta.get("source_path") or "")
            source_name = str(chunks_payload.get("source_name") or meta.get("source_name") or "")
            meta.update({"source_path": source_path, "source_name": source_name, "quality_score": quality_report.get("quality_score", meta.get("quality_score")), "quality_report": quality_report or meta.get("quality_report") or {}, "conversion_warnings": list(chunks_payload.get("conversion_warnings") or meta.get("conversion_warnings") or [])})
            created = previous["created_at"] if previous else now
            document_columns = "document_id,source_path,source_name,source_sha256,schema_version,index_version,status,quality_score,page_count,chunk_count,index_fingerprint,retrieval_corpus_fingerprint,retrieval_chunk_count,metadata_json,created_at,updated_at"
            db.execute(f"INSERT OR REPLACE INTO pipeline_documents ({document_columns}) VALUES ({','.join('?' for _ in range(16))})", (document_id, source_path, source_name, source_sha256, str(chunks_payload.get("schema_version", "")), INDEX_SCHEMA_VERSION, "ready", quality_report.get("quality_score", meta.get("quality_score")), len(page_stats), len(chunks), fingerprint, retrieval_corpus_fingerprint, retrieval_count, _json(meta), created, now)); db.execute("INSERT OR REPLACE INTO pipeline_index_manifest VALUES (?,?,?)", (document_id, _json(report), now))
    return report


def build_indexes(chunks_payloads: list[dict[str, Any]], db_path: str | Path) -> list[dict[str, Any]]:
    """Index multiple document payloads, one atomic transaction per document.

    ``build_index`` remains the compatibility API; this helper is useful for a
    directory rebuild because one malformed document does not invalidate
    already committed documents.
    """
    return [build_index(payload, db_path) for payload in chunks_payloads]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index v2 chunks into SQLite FTS5"); parser.add_argument("input", type=Path); parser.add_argument("db", type=Path); parser.add_argument("--manifest", type=Path); args = parser.parse_args(argv)
    result = build_index(read_json(str(args.input)), args.db)
    if args.manifest: write_json(str(args.manifest), result)
    else: print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
