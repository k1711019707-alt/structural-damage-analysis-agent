from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _conversion(document_id: str = "doc-a"):
    from knowledge_pipeline.contracts import BlockRecord, DocumentConversion, StageStatus

    return DocumentConversion(
        document_id=document_id,
        source_path="reference.pdf",
        source_name="reference.pdf",
        source_sha256="abc123",
        extension=".pdf",
        blocks=[
            BlockRecord(
                block_id=f"{document_id}:page:3:block:0",
                location="page:3",
                page_number=3,
                text="结构裂缝修复应先进行现场复核，确认裂缝性质、宽度和发展情况。",
                extraction_method="native:pypdf",
            )
        ],
        status=StageStatus(status="ready"),
    )


def test_stage_python_api_preserves_source_marker(tmp_path: Path) -> None:
    from knowledge_pipeline.chunk import chunk_conversion
    from knowledge_pipeline.generate import build_generation_context
    from knowledge_pipeline.index import build_index
    from knowledge_pipeline.retrieve import retrieve
    from knowledge_pipeline.rerank import rerank_payload

    chunks = chunk_conversion(_conversion(), size=80, overlap=10)
    assert chunks["chunks"][0]["source_marker"] == "[KB:doc-a:page:3]"
    db = tmp_path / "pipeline.sqlite3"
    manifest = build_index(chunks, db)
    assert manifest["chunk_count"] >= 1
    retrieved = retrieve("结构裂缝 修复", db, document_ids=["doc-a"])
    assert retrieved.chunks
    assert retrieved.chunks[0].source_marker == "[KB:doc-a:page:3]"
    reranked = rerank_payload(retrieved.to_dict())
    assert reranked.chunks[0]["original_score"] is not None
    context = build_generation_context(query="结构裂缝修复", retrieved=reranked.to_dict())
    assert context.review_status == "pending_engineer_review"


def _table_html(rows: list[list[str]]) -> str:
    return "<table>" + "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    ) + "</table>"


def test_table_dedup_preserves_same_page_tables_with_shared_long_header() -> None:
    from knowledge_pipeline.chunk import _canonical_tables
    from knowledge_pipeline.contracts import TableRecord

    header = [
        "强度指标类别",
        "混凝土强度等级设计值",
        *[f"混凝土强度等级C{grade}标准设计值" for grade in range(15, 85, 5)],
    ]
    first = TableRecord(
        table_id="doc:table:fc",
        page_number=34,
        bbox=[10.0, 10.0, 500.0, 180.0],
        html=_table_html([header, ["fc", *[str(value) for value in range(1, len(header))]]]),
        extraction_method="table:docling",
    )
    second = TableRecord(
        table_id="doc:table:fi",
        page_number=34,
        bbox=[10.0, 200.0, 500.0, 370.0],
        html=_table_html([header, ["fi", *[str(value + 20) for value in range(1, len(header))]]]),
        extraction_method="table:docling",
    )

    canonical = _canonical_tables([first, second])

    assert [table.table_id for table in canonical] == ["doc:table:fc", "doc:table:fi"]


def test_table_dedup_merges_exact_cross_backend_duplicate_with_provenance() -> None:
    from knowledge_pipeline.chunk import _canonical_tables
    from knowledge_pipeline.contracts import TableRecord

    rows = [["构件", "限值"], ["梁", "0.20 mm"]]
    docling = TableRecord(
        table_id="doc:table:docling",
        page_number=8,
        bbox=[20.0, 30.0, 480.0, 160.0],
        html=_table_html(rows),
        extraction_method="table:docling",
    )
    camelot = TableRecord(
        table_id="doc:table:camelot",
        page_number=8,
        bbox=[20.5, 30.5, 479.5, 160.5],
        html=_table_html(rows),
        extraction_method="table:camelot",
    )

    canonical = _canonical_tables([camelot, docling])

    assert len(canonical) == 1
    assert canonical[0].table_id == "doc:table:docling"
    alternatives = canonical[0].metadata["alternative_sources"]
    assert alternatives[0]["table_id"] == "doc:table:camelot"
    assert alternatives[0]["extraction_method"] == "table:camelot"


def test_table_dedup_preserves_identical_same_backend_tables_in_different_regions() -> None:
    from knowledge_pipeline.chunk import _canonical_tables
    from knowledge_pipeline.contracts import TableRecord

    rows = [["构件", "限值"], ["梁", "0.20 mm"]]
    first = TableRecord(
        table_id="doc:table:first-region",
        page_number=8,
        bbox=[20.0, 30.0, 480.0, 160.0],
        html=_table_html(rows),
        extraction_method="table:docling",
    )
    second = TableRecord(
        table_id="doc:table:second-region",
        page_number=8,
        bbox=[20.0, 300.0, 480.0, 430.0],
        html=_table_html(rows),
        extraction_method="table:docling",
    )

    canonical = _canonical_tables([first, second])

    assert [table.table_id for table in canonical] == [
        "doc:table:first-region",
        "doc:table:second-region",
    ]


def test_table_dedup_preserves_conflicting_cross_backend_tables() -> None:
    from knowledge_pipeline.chunk import _canonical_tables
    from knowledge_pipeline.contracts import TableRecord

    header = ["指标说明", *[f"混凝土强度等级C{grade}标准设计值" for grade in range(15, 85, 5)]]
    docling = TableRecord(
        table_id="doc:table:docling-conflict",
        page_number=9,
        bbox=[20.0, 30.0, 480.0, 160.0],
        html=_table_html([header, ["fc", *["10"] * (len(header) - 1)]]),
        extraction_method="table:docling",
    )
    camelot = TableRecord(
        table_id="doc:table:camelot-conflict",
        page_number=9,
        bbox=[20.5, 30.5, 479.5, 160.5],
        html=_table_html([header, ["ft", *["2"] * (len(header) - 1)]]),
        extraction_method="table:camelot",
    )

    canonical = _canonical_tables([camelot, docling])

    assert {table.table_id for table in canonical} == {
        "doc:table:docling-conflict",
        "doc:table:camelot-conflict",
    }


@pytest.mark.parametrize("candidates, expected_relevance", [([{"chunk_id": "doc-a:c1", "semantic_score": 0.9, "semantic_rank": 1}], "hit"), ([], "no_hit")])
def test_semantic_cli_emits_unified_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidates: list[dict[str, object]],
    expected_relevance: str,
) -> None:
    from knowledge_pipeline import semantic_retrieve

    monkeypatch.setattr(semantic_retrieve, "semantic_search", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(semantic_retrieve, "_semantic_scope_available", lambda *args, **kwargs: True)
    output = tmp_path / "semantic.json"

    code = semantic_retrieve.main([
        "裂缝复核",
        str(tmp_path / "semantic.npz"),
        str(tmp_path / "pipeline.sqlite3"),
        str(output),
        "--document-id",
        "doc-a",
    ])
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert code == 0
    assert payload["schema_version"] == "knowledge-semantic-retrieval.v1"
    assert payload["status"]["status"] == "ready"
    assert payload["status"]["stage_version"] == "semantic-retrieve.v1"
    assert payload["retrieval_mode"] == "semantic"
    assert payload["relevance_status"] == expected_relevance
    assert payload["scope_available"] is True
    assert payload["scope_document_ids"] == ["doc-a"]
    assert payload["candidates"] == candidates


def test_semantic_cli_writes_unavailable_contract_before_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from knowledge_pipeline import semantic_retrieve

    monkeypatch.setattr(semantic_retrieve, "_semantic_scope_available", lambda *args, **kwargs: True)

    def fail_search(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("sidecar stale")

    monkeypatch.setattr(semantic_retrieve, "semantic_search", fail_search)
    output = tmp_path / "semantic-unavailable.json"

    code = semantic_retrieve.main([
        "裂缝复核",
        str(tmp_path / "semantic.npz"),
        str(tmp_path / "pipeline.sqlite3"),
        str(output),
        "--document-id",
        "doc-a",
    ])
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert code == 1
    assert payload["schema_version"] == "knowledge-semantic-retrieval.v1"
    assert payload["status"]["status"] == "failed"
    assert payload["status"]["error"] == "semantic_unavailable:RuntimeError"
    assert payload["status"]["warnings"] == ["semantic_unavailable:RuntimeError"]
    assert payload["relevance_status"] == "unavailable"
    assert payload["scope_available"] is True
    assert payload["scope_document_ids"] == ["doc-a"]
    assert payload["candidates"] == []


def test_scope_contract_propagates_through_rerank_and_generation() -> None:
    from knowledge_pipeline.generate import build_generation_context
    from knowledge_pipeline.rerank import rerank_payload

    retrieved = {
        "query": "裂缝复核",
        "chunks": [{"chunk_id": "doc-a:c1", "document_id": "doc-a", "text": "裂缝需要复核", "metadata": {}}],
        "scope_available": True,
        "scope_document_ids": ["doc-a"],
        "retrieval_mode": "hybrid_hierarchical",
        "relevance_status": "hit",
        "status": {"status": "ready", "stage_version": "retrieve.v1", "warnings": ["upstream-warning"]},
    }

    reranked = rerank_payload(retrieved).to_dict()
    generated = build_generation_context(query="裂缝复核", retrieved=reranked).to_dict()

    assert reranked["scope_available"] is True
    assert reranked["scope_document_ids"] == ["doc-a"]
    assert reranked["status"]["warnings"] == ["upstream-warning"]
    assert generated["scope_available"] is True
    assert generated["scope_document_ids"] == ["doc-a"]
    assert generated["retrieval_warnings"] == ["upstream-warning"]
    assert generated["review_status"] == "pending_engineer_review"


def test_conversion_quality_propagates_through_chunk_and_index(tmp_path: Path) -> None:
    from knowledge_pipeline.chunk import chunk_conversion
    from knowledge_pipeline.contracts import BlockRecord, DocumentConversion, PageRecord, QualityReport, StageStatus
    from knowledge_pipeline.index import build_index

    conversion = DocumentConversion(
        document_id="doc-quality",
        source_path=str(tmp_path / "quality-source.pdf"),
        source_name="quality-source.pdf",
        source_sha256="quality-sha",
        extension=".pdf",
        blocks=[
            BlockRecord(
                block_id="doc-quality:page:1:block:0",
                location="page:1:block:0",
                page_number=1,
                text="第一页有效结构检测文本，可用于建立检索子块。",
                extraction_method="native:pymupdf",
            )
        ],
        pages=[
            PageRecord(page_number=1, extraction_method="native:pymupdf"),
            PageRecord(
                page_number=2,
                page_type="scanned",
                routing_type="scanned",
                extraction_method="none",
                needs_review=True,
                warnings=["OCR 失败：RuntimeError: backend unavailable", "页面没有可用原生文本或 OCR 文本"],
            ),
        ],
        quality_report=QualityReport(
            page_count=2,
            native_pages=1,
            low_quality_pages=[2],
            failed_pages=[2],
            warnings=["第 2 页：OCR 失败：RuntimeError: backend unavailable"],
            quality_score=0.4,
            needs_review=True,
        ),
        status=StageStatus(
            status="success_with_warnings",
            warnings=["第 2 页：OCR 失败：RuntimeError: backend unavailable"],
        ),
        metadata={"backend": "pymupdf"},
    )

    payload = chunk_conversion(conversion)
    assert payload["source_path"] == conversion.source_path
    assert payload["source_name"] == conversion.source_name
    assert payload["quality_score"] == 0.4
    assert payload["quality_report"]["failed_pages"] == [2]
    assert payload["page_inventory"][1]["page_number"] == 2
    assert payload["metadata"]["quality_score"] == 0.4

    db_path = tmp_path / "quality.sqlite3"
    report = build_index(payload, db_path)
    assert report["page_count"] == 2
    with sqlite3.connect(db_path) as connection:
        document = connection.execute(
            "SELECT source_path, source_name, quality_score, page_count, metadata_json FROM pipeline_documents WHERE document_id=?",
            ("doc-quality",),
        ).fetchone()
        failed_page = connection.execute(
            "SELECT extraction_methods_json, chunk_count, needs_review, warnings_json FROM pipeline_pages WHERE document_id=? AND page_number=2",
            ("doc-quality",),
        ).fetchone()

    assert document[:4] == (conversion.source_path, "quality-source.pdf", 0.4, 2)
    document_metadata = json.loads(document[4])
    assert document_metadata["quality_report"]["failed_pages"] == [2]
    assert document_metadata["conversion_warnings"] == ["第 2 页：OCR 失败：RuntimeError: backend unavailable"]
    assert json.loads(failed_page[0]) == ["none"]
    assert failed_page[1] == 0
    assert failed_page[2] == 1
    assert "failed_page" in json.loads(failed_page[3])
    assert "页面没有可用原生文本或 OCR 文本" in json.loads(failed_page[3])


def test_chunk_adapts_conversion_v3_html_tables_and_visual_regions() -> None:
    from knowledge_pipeline.chunk import chunk_conversion, conversion_from_payload

    payload = _conversion("doc-v3").to_dict()
    payload["tables"] = [{
        "table_id": "table-v3",
        "page_number": 4,
        "html": "<table><thead><tr><th>构件</th><th>裂缝宽度</th></tr></thead><tbody><tr><td>梁</td><td>0.20 mm</td></tr></tbody></table>",
        "extraction_method": "table:docling",
        "needs_review": True,
        "metadata": {"html_valid": True, "quality_flags": []},
    }]
    payload["visual_regions"] = [{
        "region_id": "doc-v3:page:5:region:0",
        "page_number": 5,
        "region_type": "figure",
        "ocr_text": "裂缝示意图",
        "vision_raw": "raw-model-output",
        "vision_summary": "梁端斜裂缝",
        "vision_search": "梁端 斜裂缝",
        "extraction_method": "vision:vlm",
        "metadata": {"model_generated": True},
    }]

    result = chunk_conversion(conversion_from_payload(payload))
    table_children = [item for item in result["chunks"] if item["metadata"].get("chunk_type") == "table_row"]
    visual_children = [item for item in result["chunks"] if item["metadata"].get("region_id") == "doc-v3:page:5:region:0"]
    assert table_children
    assert "构件" in table_children[0]["text"]
    assert table_children[0]["metadata"]["table_id"] == "table-v3"
    assert visual_children
    assert "梁端 斜裂缝" in visual_children[0]["metadata"]["vision_search"]
    assert result["validation"]["error_count"] == 0


def test_chunk_keeps_legacy_table_rows_compatible() -> None:
    from knowledge_pipeline.chunk import chunk_conversion, conversion_from_payload

    payload = _conversion("doc-legacy").to_dict()
    payload["schema_version"] = "knowledge-conversion.v2"
    payload["tables"] = [{
        "table_id": "table-old",
        "page_number": 2,
        "rows": [["项目", "结果"], ["裂缝", "复核"]],
        "markdown": "|项目|结果|\n|裂缝|复核|",
        "extraction_method": "table:camelot",
    }]

    result = chunk_conversion(conversion_from_payload(payload))
    assert any(item["metadata"].get("table_id") == "table-old" for item in result["chunks"])


def test_scoped_fallback_is_explicit_and_does_not_cross_document(tmp_path: Path) -> None:
    from knowledge_pipeline.chunk import chunk_conversion
    from knowledge_pipeline.index import build_index
    from knowledge_pipeline.retrieve import retrieve

    first = chunk_conversion(_conversion("doc-a"))
    second = chunk_conversion(_conversion("doc-b"))
    second["chunks"][0]["text"] = "施工方案施工前应设置安全防护和停工条件。"
    db = tmp_path / "pipeline.sqlite3"
    build_index(first, db)
    build_index(second, db)
    result = retrieve("完全不存在的词", db, document_ids=["doc-a"], allow_scoped_fallback=True)
    assert result.retrieval_mode == "scoped_fallback"
    assert result.relevance_status == "unknown"
    assert result.chunks
    assert {chunk.document_id for chunk in result.chunks} == {"doc-a"}


def test_stage_cli_contracts(tmp_path: Path) -> None:
    from knowledge_pipeline.chunk import chunk_conversion
    from knowledge_pipeline.contracts import write_json

    conversion_path = tmp_path / "conversion.json"
    chunks_path = tmp_path / "chunks.json"
    write_json(str(conversion_path), {**_conversion().to_dict()})
    command = [sys.executable, "-m", "knowledge_pipeline.chunk", str(conversion_path), str(chunks_path)]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(chunks_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "knowledge-chunks.v2"


def test_v2_index_excludes_parents_from_fts_and_hydrates_metadata(tmp_path: Path) -> None:
    from knowledge_pipeline.index import build_index
    from knowledge_pipeline.retrieve import retrieve

    payload = {
        "schema_version": "knowledge-chunks.v2",
        "document_id": "doc-v2",
        "source_sha256": "sha-v2",
        "chunks": [
            {"chunk_id": "doc-v2:parent", "location": "unit:1", "text": "父上下文", "parent_id": "doc-v2:parent", "source_marker": "[KB:doc-v2:page:1]", "metadata": {"chunk_level": "parent", "retrieval_role": "context_only", "page_numbers": [1], "text_search": "父上下文"}},
            {"chunk_id": "doc-v2:child:0", "location": "unit:1:part:0", "text": "5.3.2 结构裂缝表格行", "parent_id": "doc-v2:parent", "source_marker": "[KB:doc-v2:page:1]", "metadata": {"chunk_level": "child", "retrieval_role": "retrieval", "chunk_type": "table_row", "clause_number": "5.3.2", "table_id": "table-1", "needs_review": True, "page_numbers": [1], "text_search": "5.3.2 结构裂缝表格行"}},
        ],
    }
    db = tmp_path / "v2.sqlite3"
    manifest = build_index(payload, db)
    assert manifest["parent_count"] == 1
    assert manifest["table_chunk_count"] == 1
    result = retrieve("5.3.2", db, document_ids=["doc-v2"], top_k=1)
    assert [chunk.chunk_id for chunk in result.chunks] == ["doc-v2:child:0", "doc-v2:parent"]
    assert result.chunks[0].metadata["chunk_type"] == "table_row"
    assert result.chunks[0].metadata["needs_review"] is True
    assert result.chunks[0].metadata["table_id"] == "table-1"
    assert result.chunks[1].metadata["retrieval_role"] == "context_only"


def test_v2_index_is_idempotent_for_same_payload(tmp_path: Path) -> None:
    from knowledge_pipeline.index import build_index

    payload = {"schema_version": "knowledge-chunks.v2", "document_id": "doc-idempotent", "source_sha256": "same", "chunks": [{"chunk_id": "c", "location": "u", "text": "稳定内容", "metadata": {"retrieval_role": "retrieval", "text_search": "稳定内容"}}]}
    db = tmp_path / "idempotent.sqlite3"
    first = build_index(payload, db)
    second = build_index(payload, db)
    assert first["skipped"] is False
    assert second["skipped"] is True
    import sqlite3
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pipeline_chunks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM pipeline_chunks_fts").fetchone()[0] == 1


def test_v2_index_persists_visual_regions_and_stable_evidence_anchors(tmp_path: Path) -> None:
    from knowledge_pipeline.index import build_index
    import sqlite3

    payload = {
        "schema_version": "knowledge-chunks.v2",
        "document_id": "doc-evidence",
        "source_sha256": "evidence-sha",
        "table_diagnostics": [{"table_id": "table-empty", "page_number": 3, "extraction_method": "candidate:vector-drawings", "needs_review": True, "quality_flags": ["table_candidate_without_cells"]}],
        "image_references": [{"image_id": "image-empty", "page_number": 4, "asset_path": "page-4.png", "image_hash": "hash-4", "needs_review": True}, {"region_id": "visual:empty", "page_number": 5, "region_type": "figure", "needs_review": True}],
        "chunks": [
            {"chunk_id": "table:parent", "location": "page:1:table:1", "text": "表头：构件｜结果", "parent_id": "table:parent", "source_marker": "[KB:doc-evidence:page:1]", "metadata": {"chunk_level": "parent", "retrieval_role": "context_only", "chunk_type": "table", "table_id": "table-1", "table_source": "table:docling", "page_numbers": [1], "text_search": "表头 构件 结果"}},
            {"chunk_id": "table:child:0", "location": "page:1:table:1:rows:1-1", "text": "表头：构件｜结果\n梁｜复核", "parent_id": "table:parent", "source_marker": "[KB:doc-evidence:page:1]", "metadata": {"chunk_level": "child", "retrieval_role": "retrieval", "chunk_type": "table_row", "table_id": "table-1", "table_source": "table:docling", "page_numbers": [1], "text_search": "构件 结果 梁 复核", "header_repeated": True}},
            {"chunk_id": "visual:child", "location": "page:2:visual:r1", "text": "裂缝示意图 梁端斜裂缝", "parent_id": "visual:r1", "source_marker": "[KB:doc-evidence:page:2]", "metadata": {"chunk_level": "child", "retrieval_role": "retrieval", "chunk_type": "image_ocr", "region_id": "visual:r1", "region_type": "figure", "ocr_text": "裂缝示意图", "vision_search": "梁端斜裂缝", "vision_summary": "梁端裂缝", "vision_raw": "raw", "model_generated": True, "page_numbers": [2], "text_search": "裂缝示意图 梁端斜裂缝"}},
        ],
    }
    db = tmp_path / "evidence.sqlite3"
    manifest = build_index(payload, db)
    assert manifest["visual_region_count"] == 2
    assert manifest["visual_region_chunk_count"] == 1
    assert manifest["table_diagnostic_count"] == 1
    assert manifest["image_reference_count"] == 1
    assert manifest["visual_reference_count"] == 1
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT chunk_id FROM pipeline_tables WHERE table_id='table-1'").fetchone()[0] == "table:parent"
        assert connection.execute("SELECT COUNT(*) FROM pipeline_evidence_links WHERE evidence_type='table'").fetchone()[0] == 2
        visual = connection.execute("SELECT region_type, ocr_text, vision_search, model_generated FROM pipeline_visual_regions WHERE region_id='visual:r1'").fetchone()
        assert tuple(visual) == ("figure", "裂缝示意图", "梁端斜裂缝", 1)
        assert connection.execute("SELECT COUNT(*) FROM pipeline_chunks_fts").fetchone()[0] == 2
        assert connection.execute("SELECT chunk_id FROM pipeline_tables WHERE table_id='table-empty'").fetchone()[0] == ""
        assert connection.execute("SELECT chunk_id FROM pipeline_images WHERE image_id='image-empty'").fetchone()[0] == ""
        assert connection.execute("SELECT chunk_id FROM pipeline_visual_regions WHERE region_id='visual:empty'").fetchone()[0] == ""


def test_index_exposes_shared_hybrid_retrieval_corpus_contract(tmp_path: Path) -> None:
    from knowledge_pipeline.index import build_index
    import sqlite3

    payload = {
        "schema_version": "knowledge-chunks.v2",
        "document_id": "doc-hybrid-contract",
        "source_sha256": "hybrid-sha",
        "chunks": [
            {"chunk_id": "parent", "location": "unit:0", "text": "上下文", "parent_id": "parent", "metadata": {"chunk_level": "parent", "retrieval_role": "context_only", "text_search": "上下文"}},
            {"chunk_id": "child", "location": "unit:0:part:0", "text": "结构裂缝修复", "parent_id": "parent", "metadata": {"chunk_level": "child", "retrieval_role": "retrieval", "text_search": "结构裂缝修复", "page_numbers": [1]}},
            {"chunk_id": "empty-child", "location": "unit:1", "text": "", "metadata": {"retrieval_role": "retrieval", "text_search": ""}},
        ],
    }
    db = tmp_path / "hybrid.sqlite3"
    manifest = build_index(payload, db)
    assert manifest["retrieval_chunk_count"] == 1
    assert len(manifest["retrieval_corpus_fingerprint"]) == 64
    assert manifest["retrieval_fts_columns"] == ["text_search", "heading_text", "clause_number", "standard_number", "table_text", "image_text"]
    with sqlite3.connect(db) as connection:
        row = connection.execute("SELECT retrieval_chunk_count, retrieval_corpus_fingerprint FROM pipeline_documents WHERE document_id=?", ("doc-hybrid-contract",)).fetchone()
        assert row[0] == 1
        assert row[1] == manifest["retrieval_corpus_fingerprint"]
        assert connection.execute("SELECT COUNT(*) FROM pipeline_chunks_fts").fetchone()[0] == 1


def test_retrieval_handles_continuous_chinese_natural_query(tmp_path: Path) -> None:
    from knowledge_pipeline.index import build_index
    from knowledge_pipeline.retrieve import retrieve

    payload = {
        "schema_version": "knowledge-chunks.v2",
        "document_id": "doc-natural",
        "chunks": [
            {
                "chunk_id": "doc-natural:parent",
                "location": "unit:1",
                "text": "裂缝修复完整上下文",
                "parent_id": "doc-natural:parent",
                "source_marker": "[KB:doc-natural:page:1]",
                "metadata": {"chunk_level": "parent", "retrieval_role": "context_only", "page_numbers": [1], "text_search": "裂缝修复完整上下文"},
            },
            {
                "chunk_id": "doc-natural:child:0",
                "location": "unit:1:part:0",
                "text": "结构裂缝修复应先进行现场复核。",
                "parent_id": "doc-natural:parent",
                "source_marker": "[KB:doc-natural:page:1]",
                "metadata": {"chunk_level": "child", "retrieval_role": "retrieval", "page_numbers": [1], "text_search": "结构裂缝修复应先进行现场复核。"},
            },
        ],
    }
    db = tmp_path / "natural.sqlite3"
    build_index(payload, db)
    result = retrieve("结构裂缝如何修复", db, document_ids=["doc-natural"], top_k=1, allow_scoped_fallback=False)
    assert result.retrieval_mode == "hybrid_lexical"
    assert result.chunks[0].chunk_id == "doc-natural:child:0"
    assert result.chunks[0].metadata["retrieval_channels"]
    assert result.chunks[1].chunk_id == "doc-natural:parent"


def test_retrieval_exact_standard_and_clause_variants(tmp_path: Path) -> None:
    from knowledge_pipeline.index import build_index
    from knowledge_pipeline.retrieve import retrieve

    payload = {
        "schema_version": "knowledge-chunks.v2",
        "document_id": "doc-identifiers",
        "chunks": [
            {
                "chunk_id": "doc-identifiers:standard",
                "location": "unit:standard",
                "text": "GB 55021-2021 结构要求",
                "source_marker": "[KB:doc-identifiers:page:1]",
                "metadata": {"retrieval_role": "retrieval", "standard_number": "GB55021-2021", "page_numbers": [1], "text_search": "GB 55021-2021 结构要求"},
            },
            {
                "chunk_id": "doc-identifiers:clause",
                "location": "unit:clause",
                "text": "5.3.2 裂缝检测",
                "source_marker": "[KB:doc-identifiers:page:2]",
                "metadata": {"retrieval_role": "retrieval", "clause_number": "5.3.2", "page_numbers": [2], "text_search": "5.3.2 裂缝检测"},
            },
        ],
    }
    db = tmp_path / "identifiers.sqlite3"
    build_index(payload, db)
    standard = retrieve("GB 55021-2021", db, document_ids=["doc-identifiers"], top_k=1, allow_scoped_fallback=False)
    assert standard.chunks[0].chunk_id == "doc-identifiers:standard"
    assert "standard_exact" in standard.chunks[0].metadata["retrieval_channels"]
    clause = retrieve("第5.3.2条", db, document_ids=["doc-identifiers"], top_k=1, allow_scoped_fallback=False)
    assert clause.chunks[0].chunk_id == "doc-identifiers:clause"
    assert "clause_exact" in clause.chunks[0].metadata["retrieval_channels"]


def test_local_semantic_sidecar_and_hybrid_fusion(tmp_path: Path) -> None:
    from knowledge_pipeline.embed import build_embedding_index
    from knowledge_pipeline.index import build_index
    from knowledge_pipeline.retrieve import retrieve
    from knowledge_pipeline.semantic_retrieve import SemanticRetriever

    payload = {
        "schema_version": "knowledge-chunks.v2",
        "document_id": "doc-semantic",
        "chunks": [
            {"chunk_id": "doc-semantic:c0", "location": "unit:0", "text": "梁体出现斜向裂缝，应检查剪切受力。", "source_marker": "[KB:doc-semantic:page:1]", "metadata": {"retrieval_role": "retrieval", "page_numbers": [1], "text_search": "梁体出现斜向裂缝，应检查剪切受力。"}},
            {"chunk_id": "doc-semantic:c1", "location": "unit:1", "text": "柱脚混凝土剥落，应检查钢筋锈蚀。", "source_marker": "[KB:doc-semantic:page:2]", "metadata": {"retrieval_role": "retrieval", "page_numbers": [2], "text_search": "柱脚混凝土剥落，应检查钢筋锈蚀。"}},
        ],
    }

    class FakeEmbedder:
        def encode(self, texts, **kwargs):
            return [[1.0, 0.0] if "裂缝" in text or "裂纹" in text else [0.0, 1.0] for text in texts]

    db = tmp_path / "semantic.sqlite3"
    build_index(payload, db)
    sidecar = tmp_path / "semantic.npz"
    build_embedding_index(db, sidecar, model_name="fake", embedder=FakeEmbedder())
    semantic = SemanticRetriever(sidecar, embedder=FakeEmbedder())
    candidates = semantic.search("梁体发现裂纹", db, document_ids=["doc-semantic"], top_k=1)
    assert candidates[0]["chunk_id"] == "doc-semantic:c0"
    result = retrieve("梁体发现裂纹", db, document_ids=["doc-semantic"], top_k=1, allow_scoped_fallback=False, semantic_retriever=semantic)
    assert result.retrieval_mode == "hybrid_semantic"
    assert result.chunks[0].chunk_id == "doc-semantic:c0"
    assert "semantic" in result.chunks[0].metadata["retrieval_channels"]
    assert "bm25" in result.chunks[0].metadata["retrieval_channels"] or "like" in result.chunks[0].metadata["retrieval_channels"]
    assert result.chunks[0].metadata["semantic_score"] > 0.9
    assert result.chunks[0].metadata["fusion_weights"]["vector"] == 2.0
    assert result.chunks[0].metadata["channel_contributions"]

    payload["chunks"].append({"chunk_id": "doc-semantic:c2", "location": "unit:2", "text": "新增检索子块", "source_marker": "[KB:doc-semantic:page:3]", "metadata": {"retrieval_role": "retrieval", "page_numbers": [3], "text_search": "新增检索子块"}})
    build_index(payload, db)
    stale = retrieve("梁体发现裂纹", db, document_ids=["doc-semantic"], top_k=1, allow_scoped_fallback=False, semantic_retriever=semantic)
    assert stale.retrieval_mode == "hybrid_lexical"
    assert stale.status.warnings
    assert stale.status.warnings[0] == "semantic_unavailable:RuntimeError"


def test_hierarchical_retrieval_expands_same_heading_path(tmp_path: Path) -> None:
    from knowledge_pipeline.index import build_index
    from knowledge_pipeline.retrieve import retrieve

    payload = {
        "schema_version": "knowledge-chunks.v2", "document_id": "doc-section",
        "chunks": [
            {"chunk_id": "p0", "location": "unit:0", "text": "第一节完整父上下文", "parent_id": "p0", "source_marker": "[KB:doc-section:page:1]", "metadata": {"chunk_level": "parent", "retrieval_role": "context_only", "heading_path": ["第一章", "1.1 裂缝"], "page_numbers": [1], "text_search": "第一节完整父上下文"}},
            {"chunk_id": "c0", "location": "unit:0:part:0", "text": "裂缝检测应记录宽度。", "parent_id": "p0", "source_marker": "[KB:doc-section:page:1]", "metadata": {"chunk_level": "child", "retrieval_role": "retrieval", "heading_path": ["第一章", "1.1 裂缝"], "page_numbers": [1], "text_search": "裂缝检测应记录宽度。"}},
            {"chunk_id": "c1", "location": "unit:1:part:0", "text": "裂缝处理应结合成因。", "parent_id": "p1", "source_marker": "[KB:doc-section:page:2]", "metadata": {"chunk_level": "child", "retrieval_role": "retrieval", "heading_path": ["第一章", "1.1 裂缝"], "page_numbers": [2], "text_search": "裂缝处理应结合成因。"}},
        ],
    }
    db = tmp_path / "section.sqlite3"
    build_index(payload, db)
    result = retrieve("裂缝检测", db, document_ids=["doc-section"], top_k=1, max_chars=1000, allow_scoped_fallback=False, expansion_mode="subsection")
    assert result.context_groups
    assert result.chunks[0].metadata["anchor"] is True
    assert any(chunk.chunk_id == "c1" and chunk.metadata["expanded"] for chunk in result.chunks)
    assert result.context_groups[0]["expansion_level"] == "subsection"


def test_damage_summary_generation_prompt_preserves_review_boundary() -> None:
    from knowledge_pipeline.generate import build_generation_context

    result = build_generation_context(
        query="结构裂缝如何判断？",
        retrieved={"chunks": [{"chunk_id": "c", "text": "裂缝证据", "source_marker": "[KB:doc:page:1]", "metadata": {"needs_review": True}}]},
        generation_mode="damage-grounded-summary",
    )
    assert "损伤事实" in result.prompt
    assert "不得仅凭外观直接宣布结构安全等级" in result.prompt
    assert result.review_status == "pending_engineer_review"
    assert result.status.warnings


def test_generation_groups_hybrid_evidence_and_propagates_warnings() -> None:
    from knowledge_pipeline.generate import build_generation_context

    child_text = "文档：规范\n内容：\n5.3.2 裂缝宽度应复核。\n来源：[KB:doc:page:1]"
    result = build_generation_context(
        query="裂缝宽度如何复核",
        retrieved={
            "retrieval_mode": "hybrid_semantic",
            "relevance_status": "hit",
            "status": {"warnings": ["semantic sidecar used with fallback model"]},
            "anchors": [{"chunk_id": "c1"}],
            "context_groups": [{"anchor_chunk_ids": ["c1"], "chunk_ids": ["c1", "p1"], "heading_path": ["第五章", "5.3 裂缝"]}],
            "chunks": [
                {"chunk_id": "c1", "parent_id": "p1", "text": child_text, "source_marker": "[KB:doc:page:1]", "metadata": {"retrieval_role": "retrieval", "retrieval_channels": ["bm25", "semantic"], "anchor": True, "chunk_type": "table_row", "table_id": "t1", "needs_review": True, "text_raw": "5.3.2 裂缝宽度应复核。"}},
                {"chunk_id": "p1", "parent_id": "p1", "text": child_text, "source_marker": "[KB:doc:page:1]", "metadata": {"retrieval_role": "context_only", "context_for": "c1", "chunk_type": "table", "table_id": "t1", "text_raw": "5.3.2 裂缝宽度应复核。"}},
                {"chunk_id": "v1", "text": "梁端存在斜裂缝", "source_marker": "[KB:doc:page:2]", "metadata": {"retrieval_role": "retrieval", "retrieval_channels": ["semantic"], "chunk_type": "image_ocr", "region_id": "r1", "model_generated": True, "needs_review": True}},
            ],
        },
        generation_mode="damage-grounded-summary",
    )
    assert result.retrieval_mode == "hybrid_semantic"
    assert result.retrieval_warnings == ["semantic sidecar used with fallback model"]
    assert "直接召回证据" in result.prompt
    assert "模型生成视觉描述" in result.prompt
    assert "重复上下文已省略" in result.prompt
    assert result.evidence_groups[0]["items"][1]["included"] is False
    assert result.review_status == "pending_engineer_review"


def test_runtime_facade_preserves_hierarchical_metadata(tmp_path: Path) -> None:
    from knowledge_pipeline.index import build_index
    from knowledge_pipeline.retrieve import retrieve
    from runtime.knowledge_base import KnowledgeBase, pipeline_search_with_scope

    payload = {
        "schema_version": "knowledge-chunks.v2", "document_id": "doc-runtime",
        "chunks": [
            {"chunk_id": "p", "location": "unit:0", "text": "父上下文", "parent_id": "p", "source_marker": "[KB:doc-runtime:page:1]", "metadata": {"chunk_level": "parent", "retrieval_role": "context_only", "heading_path": ["第一章", "1.1 裂缝"], "page_numbers": [1], "text_search": "父上下文"}},
            {"chunk_id": "c", "location": "unit:0:part:0", "text": "裂缝检测", "parent_id": "p", "source_marker": "[KB:doc-runtime:page:1]", "metadata": {"chunk_level": "child", "retrieval_role": "retrieval", "heading_path": ["第一章", "1.1 裂缝"], "page_numbers": [1], "text_search": "裂缝检测"}},
        ],
    }
    db = tmp_path / "runtime.sqlite3"
    build_index(payload, db)
    result = retrieve("裂缝检测", db, top_k=1, expansion_mode="subsection")
    assert result.context_groups
    # The facade itself targets the legacy KB schema; verify conversion of
    # pipeline records preserves the new metadata contract independently.
    assert result.chunks[0].metadata["anchor"] is True
    assert result.chunks[1].metadata["expanded"] is True
