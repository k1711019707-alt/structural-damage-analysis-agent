"""Generate traceable document and retrieval-QA benchmark assets.

The generator is intentionally read-only with respect to the active production
database and source documents. Automatically inferred relevant chunks are
candidate annotations and must be reviewed before being used as gold labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.retrieve import retrieve
from runtime.rag_production import active_rag_status


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _manifest_documents(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("document_id")): dict(item)
        for item in (status.get("manifest", {}).get("source_documents") or [])
        if item.get("document_id")
    }


def build_document_records(db_path: Path, status: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_docs = _manifest_documents(status)
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        documents = db.execute("SELECT * FROM pipeline_documents ORDER BY document_id").fetchall()
        for document in documents:
            document_id = str(document["document_id"])
            pages = db.execute(
                "SELECT page_number, extraction_methods_json, chunk_count, needs_review, warnings_json "
                "FROM pipeline_pages WHERE document_id=? ORDER BY page_number",
                (document_id,),
            ).fetchall()
            warning_pages = []
            review_pages = []
            page_types: dict[str, int] = {}
            for page in pages:
                warnings = json.loads(page["warnings_json"] or "[]")
                methods = json.loads(page["extraction_methods_json"] or "[]")
                if warnings:
                    warning_pages.append({"page_number": page["page_number"], "warnings": warnings})
                if page["needs_review"]:
                    review_pages.append(int(page["page_number"]))
                page_type = "ocr" if any("ocr" in str(method).lower() for method in methods) else "native_or_layout"
                page_types[page_type] = page_types.get(page_type, 0) + 1
            child_count = db.execute(
                "SELECT COUNT(*) FROM pipeline_chunks WHERE document_id=? AND retrieval_role='retrieval'",
                (document_id,),
            ).fetchone()[0]
            cross_page_count = db.execute(
                "SELECT COUNT(*) FROM pipeline_chunks WHERE document_id=? AND retrieval_role='retrieval' AND is_cross_page=1",
                (document_id,),
            ).fetchone()[0]
            manifest = manifest_docs.get(document_id, {})
            rows.append(
                {
                    "record_id": f"document:{document_id}",
                    "document_id": document_id,
                    "source_name": manifest.get("source_name") or document["source_name"],
                    "source_path": manifest.get("source_path") or document["source_path"],
                    "source_sha256": manifest.get("source_sha256") or document["source_sha256"],
                    "database_sha256": status.get("database", {}).get("sha256", ""),
                    "schema_version": document["schema_version"],
                    "index_version": document["index_version"],
                    "status": document["status"],
                    "page_count": int(document["page_count"]),
                    "chunk_count": int(document["chunk_count"]),
                    "retrieval_child_count": int(child_count),
                    "cross_page_child_count": int(cross_page_count),
                    "page_type_counts": page_types,
                    "warning_pages": warning_pages,
                    "review_pages": review_pages,
                    "recommended_review_pages": sorted({
                        *[int(item["page_number"]) for item in warning_pages],
                        *review_pages,
                    })[:30],
                    "annotation_status": "metadata_auto_generated",
                }
            )
    return rows


def _first_values(db: sqlite3.Connection, sql: str, limit: int = 5) -> list[str]:
    return [str(row[0]) for row in db.execute(sql).fetchall() if str(row[0]).strip()][:limit]


def _query_specs(db: sqlite3.Connection) -> list[dict[str, Any]]:
    raw_standards = _first_values(
        db,
        "SELECT standard_number FROM pipeline_chunks WHERE retrieval_role='retrieval' AND standard_number<>'' GROUP BY standard_number ORDER BY standard_number",
        80,
    )
    standards = []
    for value in raw_standards:
        match = re.search(r"(?:GB|JGJ|CECS|DBJ)\s*[-—]?\s*\d{2,6}(?:\s*[-—]\s*\d{4})?", value, re.I)
        if match:
            normalized = re.sub(r"\s+", "", match.group(0)).replace("—", "-")
            if normalized not in standards:
                standards.append(normalized)
        if len(standards) >= 6:
            break
    raw_clauses = _first_values(
        db,
        "SELECT clause_number FROM pipeline_chunks WHERE retrieval_role='retrieval' AND clause_number<>'' GROUP BY clause_number ORDER BY clause_number",
        80,
    )
    clauses = []
    for value in raw_clauses:
        if re.fullmatch(r"[1-9]\d*(?:\.\d+){1,4}", value) and value not in clauses:
            clauses.append(value)
        if len(clauses) >= 6:
            break
    specs: list[dict[str, Any]] = [
        {"query_id": "natural-001", "query": "结构裂缝如何修复", "query_type": "natural_chinese", "difficulty": "medium"},
        {"query_id": "natural-002", "query": "混凝土结构可靠性鉴定的基本程序是什么", "query_type": "natural_chinese", "difficulty": "hard"},
        {"query_id": "natural-003", "query": "建筑结构加固工程施工质量验收有哪些要求", "query_type": "natural_chinese", "difficulty": "medium"},
        {"query_id": "synonym-001", "query": "裂纹处理和修补方法", "query_type": "synonym", "difficulty": "medium"},
        {"query_id": "table-001", "query": "裂缝宽度等级如何划分", "query_type": "table", "difficulty": "hard"},
        {"query_id": "cross-page-001", "query": "跨页条款的完整要求和后续措施", "query_type": "cross_page", "difficulty": "hard"},
        {"query_id": "no-answer-001", "query": "钢结构焊缝超声检测的评定等级", "query_type": "no_answer", "difficulty": "medium", "expected_no_answer": True},
        {"query_id": "risk-001", "query": "仅凭裂缝照片能否直接判定结构安全", "query_type": "high_risk_review_boundary", "difficulty": "hard", "expected_engineer_review": True},
    ]
    for index, standard in enumerate(standards, 1):
        specs.append({
            "query_id": f"standard-{index:03d}",
            "query": standard,
            "query_type": "standard_identifier",
            "difficulty": "easy",
        })
    for index, clause in enumerate(clauses, 1):
        specs.append({
            "query_id": f"clause-{index:03d}",
            "query": f"第{clause}条",
            "query_type": "clause_identifier",
            "difficulty": "easy",
        })
    # Scope records are created after candidate retrieval so the scope is a
    # real document ID rather than a guessed source name.
    specs.append({"query_id": "scope-001", "query": "结构裂缝如何修复", "query_type": "scope_isolation", "difficulty": "hard", "scope_from_candidate": True})
    return specs


def build_retrieval_records(db_path: Path, status: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as db:
        specs = _query_specs(db)
    for spec in specs:
        result = retrieve(
            spec["query"],
            db_path,
            top_k=top_k,
            max_chars=10000,
            allow_scoped_fallback=False,
            expansion_mode="subsection",
        )
        candidate_chunks = [
            {
                "chunk_id": item.chunk_id,
                "document_id": item.document_id,
                "source_marker": item.source_marker,
                "retrieval_channels": item.metadata.get("retrieval_channels", []),
                "anchor": bool(item.metadata.get("anchor")),
                "expanded": bool(item.metadata.get("expanded")),
                "needs_review": bool(item.metadata.get("needs_review")),
            }
            for item in result.chunks
            if item.metadata.get("retrieval_role") == "retrieval"
        ]
        anchor_chunks = [item for item in candidate_chunks if item["anchor"]]
        candidate_docs = list(dict.fromkeys(item["document_id"] for item in candidate_chunks))
        row = {
            "record_id": f"query:{spec['query_id']}",
            "query_id": spec["query_id"],
            "query": spec["query"],
            "query_type": spec["query_type"],
            "difficulty": spec["difficulty"],
            "scope_document_ids": [],
            "relevant_documents": list(dict.fromkeys(item["document_id"] for item in anchor_chunks)) if not spec.get("expected_no_answer") else [],
            "relevant_chunk_ids": [item["chunk_id"] for item in anchor_chunks] if not spec.get("expected_no_answer") else [],
            "candidate_chunks": candidate_chunks,
            "retrieval_observation": {
                "mode": result.retrieval_mode,
                "relevance_status": result.relevance_status,
                "anchor_count": len(result.anchors),
                "returned_chunk_count": len(result.chunks),
                "context_group_count": len(result.context_groups),
            },
            "expected_no_answer": bool(spec.get("expected_no_answer", False)),
            "expected_engineer_review": bool(spec.get("expected_engineer_review", False)),
            "annotation_status": "needs_human_review",
            "gold_label": False,
            "database_sha256": status.get("database", {}).get("sha256", ""),
        }
        if spec.get("scope_from_candidate") and candidate_docs:
            row["scope_document_ids"] = [candidate_docs[0]]
            scoped = retrieve(spec["query"], db_path, document_ids=row["scope_document_ids"], top_k=top_k, allow_scoped_fallback=False, expansion_mode="subsection")
            row["scope_observation"] = {
                "mode": scoped.retrieval_mode,
                "result_document_ids": list(dict.fromkeys(item.document_id for item in scoped.chunks)),
                "scope_leakage": any(item.document_id not in row["scope_document_ids"] for item in scoped.chunks),
            }
        records.append(row)
    return records


def generate(output_dir: Path) -> dict[str, Any]:
    status = active_rag_status()
    database = status.get("database", {})
    db_path = Path(str(database.get("path", "")))
    if not database.get("healthy") or not db_path.is_file():
        raise RuntimeError("active production v2 database is not healthy or does not exist")
    documents = build_document_records(db_path, status)
    retrieval = build_retrieval_records(db_path, status)
    _write_jsonl(output_dir / "document_benchmark.jsonl", documents)
    _write_jsonl(output_dir / "retrieval_qa_benchmark.jsonl", retrieval)
    manifest = {
        "schema_version": "rag-benchmark.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_path": str(db_path),
        "database_sha256": database.get("sha256", ""),
        "document_count": len(documents),
        "query_count": len(retrieval),
        "candidate_annotations_require_human_review": True,
        "semantic_index_path": status.get("manifest", {}).get("semantic_index_path", ""),
        "source_read_only": True,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate traceable RAG benchmark datasets")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "benchmarks" / "rag")
    args = parser.parse_args()
    print(json.dumps(generate(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
