from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "scripts" / "formal_rag_benchmark.py"
SPEC = importlib.util.spec_from_file_location("formal_rag_benchmark", MODULE_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE pipeline_documents (
              document_id TEXT PRIMARY KEY, source_path TEXT, source_name TEXT,
              source_sha256 TEXT, page_count INTEGER, retrieval_chunk_count INTEGER,
              quality_score REAL
            );
            CREATE TABLE pipeline_chunks (
              chunk_id TEXT PRIMARY KEY, document_id TEXT, text TEXT, text_search TEXT,
              text_raw TEXT, parent_id TEXT, source_marker TEXT, retrieval_role TEXT,
              chunk_type TEXT, clause_number TEXT, heading_path_json TEXT,
              page_numbers_json TEXT, primary_page INTEGER, quality_flags_json TEXT,
              is_cross_page INTEGER, standard_number TEXT, table_id TEXT,
              needs_review INTEGER
            );
            CREATE TABLE pipeline_chunk_pages (chunk_id TEXT, document_id TEXT, page_number INTEGER);
            CREATE TABLE pipeline_tables (table_id TEXT, document_id TEXT, chunk_id TEXT);
            """
        )
        db.execute(
            "INSERT INTO pipeline_documents VALUES (?,?,?,?,?,?,?)",
            ("doc-1", "source.pdf", "source.pdf", "abc", 1, 1, 1.0),
        )
        db.execute(
            "INSERT INTO pipeline_chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chunk-1", "doc-1", "内容：构件厚度不得小于20mm。\n来源：[KB:doc-1:page:1]",
                "构件厚度不得小于20mm", "构件厚度不得小于20mm。", "parent-1",
                "[KB:doc-1:page:1]", "retrieval", "paragraph", "1.1", "[\"一般规定\"]",
                "[1]", 1, "[]", 0, "GB50000-2026", "", 0,
            ),
        )


def _candidate() -> dict:
    return {
        "schema_version": benchmark.SCHEMA_VERSION,
        "question_id": "exact-001",
        "question_type": "exact_identifier",
        "question": "第1.1条是什么？",
        "answerable": True,
        "expected_no_answer": False,
        "scope_document_ids": ["doc-1"],
        "candidate_relevant_chunk_ids": ["chunk-1"],
        "evidence": [{
            "chunk_id": "chunk-1", "document_id": "doc-1", "page_numbers": [1],
            "source_marker": "[KB:doc-1:page:1]", "source_sha256": "abc",
        }],
        "annotation_status": "needs_human_review",
        "gold_label": False,
        "gold_answer": "",
        "review": benchmark._review_template(True),
    }


def test_generated_contract_has_fixed_total_and_non_gold_defaults() -> None:
    assert sum(benchmark.QUOTAS.values()) == 150
    assert benchmark._review_template(True)["original_pdf_opened"] is False
    assert benchmark._review_template(True)["manual_relevant_chunk_ids"] == []


def test_validate_rejects_wrong_quota_and_auto_gold(tmp_path: Path) -> None:
    db_path = tmp_path / "benchmark.sqlite3"
    _database(db_path)
    record = _candidate()
    record["gold_label"] = True
    result = benchmark.validate_records([record], db_path)
    assert result["valid"] is False
    assert any("record_count" in error for error in result["errors"])
    assert any("promoted to Gold" in error for error in result["errors"])


def test_publication_gate_rejects_unreviewed_record(tmp_path: Path) -> None:
    db_path = tmp_path / "benchmark.sqlite3"
    _database(db_path)
    review_path = tmp_path / "review.jsonl"
    review_path.write_text(json.dumps(_candidate(), ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(benchmark.BenchmarkError, match="Gold publication rejected"):
        benchmark.publish(review_path, db_path, tmp_path / "gold")
    assert not (tmp_path / "gold" / "gold.jsonl").exists()


def test_publication_accepts_completed_pdf_review(tmp_path: Path) -> None:
    db_path = tmp_path / "benchmark.sqlite3"
    _database(db_path)
    record = _candidate()
    record["gold_answer"] = "构件厚度不得小于20mm。"
    record["review"].update({
        "original_pdf_opened": True,
        "page_verified": True,
        "text_verified": True,
        "answer_verified": True,
        "reviewer_id": "reviewer-a",
        "reviewed_at": "2026-09-18T00:00:00+00:00",
        "manual_relevant_chunk_ids": ["chunk-1"],
        "relevance_labels": [{"chunk_id": "chunk-1", "label": "direct"}],
    })
    review_path = tmp_path / "review.jsonl"
    review_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = benchmark.publish(review_path, db_path, tmp_path / "gold")
    assert manifest["dataset_status"] == "gold_reviewed"
    gold = json.loads((tmp_path / "gold" / "gold.jsonl").read_text(encoding="utf-8"))
    assert gold["gold_label"] is True
    assert gold["relevant_chunk_ids"] == ["chunk-1"]
