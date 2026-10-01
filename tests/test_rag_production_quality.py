from __future__ import annotations

import json
import hashlib
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace


def _indexed_database(tmp_path: Path, *, document_id: str = "doc-quality", digest: str = "sha-quality") -> Path:
    from knowledge_pipeline.index import build_index

    payload = {
        "schema_version": "knowledge-chunks.v2",
        "document_id": document_id,
        "source_sha256": digest,
        "metadata": {
            "source_path": str((tmp_path / f"{document_id}.pdf").resolve()),
            "source_name": f"{document_id}.pdf",
            "quality_score": 0.95,
            "page_count": 1,
            "quality_report": {
                "page_count": 1,
                "failed_pages": [],
                "low_quality_pages": [],
                "warnings": [],
                "quality_score": 0.95,
            },
            "conversion_warnings": [],
        },
        "chunks": [{
            "chunk_id": f"{document_id}:child:0",
            "location": "page:1",
            "text": "GB 55021-2021 结构裂缝修复应先复核裂缝性质。",
            "source_marker": f"[KB:{document_id}:page:1]",
            "metadata": {
                "retrieval_role": "retrieval",
                "chunk_level": "child",
                "page_numbers": [1],
                "text_search": "GB 55021-2021 结构裂缝修复应先复核裂缝性质",
            },
        }],
    }
    db = tmp_path / "candidate.sqlite3"
    build_index(payload, db)
    return db


def _semantic_sidecar(db: Path, path: Path) -> Path:
    import numpy as np

    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT chunk_id,content_hash FROM pipeline_chunks "
            "WHERE retrieval_role='retrieval' AND text_search<>'' ORDER BY document_id,location,chunk_id"
        ).fetchall()
    identity = [(str(row[0]), str(row[1])) for row in rows]
    fingerprint = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    np.savez_compressed(
        path,
        vectors=np.ones((len(identity), 3), dtype=np.float32),
        chunk_ids=np.asarray([item[0] for item in identity]),
        content_hashes=np.asarray([item[1] for item in identity]),
    )
    Path(str(path) + ".manifest.json").write_text(
        json.dumps({
            "schema_version": "knowledge-embeddings.v1",
            "count": len(identity),
            "dimension": 3,
            "source_fingerprint": fingerprint,
        }),
        encoding="utf-8",
    )
    return path


def _activation_inputs(tmp_path: Path, db: Path, production, validator) -> dict[str, object]:
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.executescript(
            "CREATE TABLE documents(document_id TEXT,status TEXT,folder_id TEXT);"
            "CREATE TABLE folders(folder_id TEXT,parent_id TEXT);"
            "CREATE TABLE chunks(chunk_id TEXT,document_id TEXT);"
            "INSERT INTO folders VALUES ('root','');"
            "INSERT INTO documents VALUES ('doc-quality','ready','root');"
            "INSERT INTO chunks VALUES ('c','doc-quality');"
        )
    health = production.inspect_v2_database(db, strict=True)
    query = tmp_path / "query.json"
    scope = tmp_path / "scope.json"
    build = tmp_path / "build.json"
    report = tmp_path / "validation.json"
    query.write_text(json.dumps([{"query": "GB 55021-2021", "expected_standards": ["GB 55021-2021"]}]), encoding="utf-8")
    scope.write_text(json.dumps([{"folder_ids": ["root"]}]), encoding="utf-8")
    build.write_text(json.dumps({"database": {"sha256": health["sha256"]}, "required_gate": {"passed": True}, "canonical_document_count": 1}), encoding="utf-8")
    validator.validate(db, report, query_expectations_path=query, build_manifest=build, legacy_db=legacy, scope_expectations_path=scope)
    return {
        "validation_report_path": report,
        "query_expectations_path": query,
        "scope_expectations_path": scope,
        "build_manifest_path": build,
        "legacy_db_path": legacy,
        "require_v2_scope": False,
    }


def test_inspect_requires_current_schema_metadata_and_fts_parity(tmp_path: Path) -> None:
    from runtime.rag_production import inspect_v2_database

    db = _indexed_database(tmp_path)
    healthy = inspect_v2_database(db, strict=True)
    assert healthy["healthy"] is True
    assert healthy["schema_compatible"] is True
    assert healthy["metadata_coverage"]["quality_score"]["ratio"] == 1.0
    assert healthy["fts_parity"] is True

    with closing(sqlite3.connect(db)) as connection:
        connection.execute("DELETE FROM pipeline_chunks_fts")
        connection.commit()
    broken = inspect_v2_database(db, strict=True)
    assert broken["healthy"] is False
    assert "retrieval_fts_count_mismatch" in broken["integrity_errors"]


def test_inspect_old_schema_is_diagnosable_but_not_strictly_healthy(tmp_path: Path) -> None:
    from runtime.rag_production import inspect_v2_database

    db = tmp_path / "old.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.executescript("""
        CREATE TABLE pipeline_documents(document_id TEXT PRIMARY KEY);
        CREATE TABLE pipeline_chunks(chunk_id TEXT PRIMARY KEY, retrieval_role TEXT, text_search TEXT);
        CREATE VIRTUAL TABLE pipeline_chunks_fts USING fts5(chunk_id, text_search);
        INSERT INTO pipeline_documents VALUES ('old');
        INSERT INTO pipeline_chunks VALUES ('c', 'retrieval', '裂缝');
        INSERT INTO pipeline_chunks_fts VALUES ('c', '裂缝');
        """)
    result = inspect_v2_database(db, strict=True)
    assert result["healthy"] is False
    assert result["structural_healthy"] is True
    assert result["reason"] == "schema_incompatible"
    assert result["schema_errors"]


def test_active_status_rejects_manifest_sha_mismatch(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production

    db = _indexed_database(tmp_path)
    manifest = tmp_path / "active_rag.json"
    manifest.write_text(json.dumps({"database_path": str(db), "database_sha256": "wrong"}), encoding="utf-8")
    monkeypatch.setattr(production, "active_rag_manifest_path", lambda: manifest)
    result = production.active_rag_status()
    assert result["active"] is False
    assert result["legacy_fallback"] is True
    assert result["reason"] == "database_sha256_mismatch"


def test_sha_mismatch_returns_before_sqlite_open(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production

    db = _indexed_database(tmp_path)
    opened: list[bool] = []
    monkeypatch.setattr(production, "_readonly_sqlite", lambda path: opened.append(True))
    result = production.inspect_v2_database(db, expected_sha256="not-the-real-sha", strict=True)
    assert result["healthy"] is False
    assert result["reason"] == "database_sha256_mismatch"
    assert opened == []


def test_active_status_rejects_manifest_without_sha(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production

    db = _indexed_database(tmp_path)
    manifest = tmp_path / "active_rag.json"
    manifest.write_text(json.dumps({"database_path": str(db)}), encoding="utf-8")
    monkeypatch.setattr(production, "active_rag_manifest_path", lambda: manifest)
    result = production.active_rag_status()
    assert result["active"] is False
    assert result["reason"] == "manifest_database_sha256_missing"


def test_committed_wal_state_blocks_health_and_activation(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    db = _indexed_database(tmp_path)
    activation_kwargs = _activation_inputs(tmp_path, db, production, validator)
    active_manifest = production.active_rag_manifest_path()
    active_manifest_before = active_manifest.read_bytes() if active_manifest.is_file() else None
    writer = sqlite3.connect(db)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].casefold() == "wal"
        writer.execute("UPDATE pipeline_documents SET source_name='wal-only.pdf'")
        writer.commit()
        wal_path = Path(f"{db}-wal")
        assert wal_path.is_file() and wal_path.stat().st_size > 0

        health = production.inspect_v2_database(db, strict=True)
        assert health["healthy"] is False
        assert health["reason"] == "sqlite_wal_present"
        try:
            production.activate_rag(db, **activation_kwargs)
        except ValueError as exc:
            assert "sqlite_wal_present" in str(exc)
        else:
            raise AssertionError("a candidate with committed WAL state must not activate")
        assert (active_manifest.read_bytes() if active_manifest.is_file() else None) == active_manifest_before
    finally:
        writer.close()


def test_active_status_binds_semantic_identity_and_rejects_npz_replacement(tmp_path: Path, monkeypatch) -> None:
    import numpy as np
    import runtime.rag_production as production

    db = _indexed_database(tmp_path)
    sidecar = _semantic_sidecar(db, tmp_path / "semantic.npz")
    health = production.inspect_v2_database(db, semantic_index_path=sidecar, strict=True)
    semantic = health["semantic"]
    active_manifest = tmp_path / "active_rag.json"
    active_manifest.write_text(json.dumps({
        "database_path": str(db),
        "database_sha256": health["sha256"],
        "semantic_index_path": str(sidecar),
        "semantic_index_sha256": semantic["index_sha256"],
        "semantic_manifest_sha256": semantic["manifest_sha256"],
    }), encoding="utf-8")
    monkeypatch.setattr(production, "active_rag_manifest_path", lambda: active_manifest)

    status = production.active_rag_status()
    assert status["active"] is True
    assert status["semantic"] == semantic
    assert status["semantic"]["path"] == str(sidecar.resolve())

    with np.load(sidecar, allow_pickle=False) as arrays:
        vectors = np.asarray(arrays["vectors"]) * 2
        chunk_ids = np.asarray(arrays["chunk_ids"])
        content_hashes = np.asarray(arrays["content_hashes"])
    np.savez_compressed(sidecar, vectors=vectors, chunk_ids=chunk_ids, content_hashes=content_hashes)
    replaced = production.active_rag_status()
    assert replaced["active"] is False
    assert replaced["reason"] == "semantic_index_sha256_mismatch"


def test_active_status_detects_companion_manifest_switch_during_validation(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production

    db = _indexed_database(tmp_path)
    sidecar = _semantic_sidecar(db, tmp_path / "semantic.npz")
    health = production.inspect_v2_database(db, semantic_index_path=sidecar, strict=True)
    semantic = health["semantic"]
    active_manifest = tmp_path / "active_rag.json"
    active_manifest.write_text(json.dumps({
        "database_path": str(db),
        "database_sha256": health["sha256"],
        "semantic_index_path": str(sidecar),
        "semantic_index_sha256": semantic["index_sha256"],
        "semantic_manifest_sha256": semantic["manifest_sha256"],
    }), encoding="utf-8")
    monkeypatch.setattr(production, "active_rag_manifest_path", lambda: active_manifest)
    real_load = production.load_semantic_arrays

    def switching_load(*args, **kwargs):
        arrays = real_load(*args, **kwargs)
        manifest_path = Path(str(sidecar) + ".manifest.json")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["switched"] = True
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        return arrays

    monkeypatch.setattr(production, "load_semantic_arrays", switching_load)
    status = production.active_rag_status()
    assert status["active"] is False
    assert status["reason"] == "semantic_manifest_changed_during_validation"


def test_activation_records_and_replays_semantic_digests(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    db = _indexed_database(tmp_path)
    sidecar = _semantic_sidecar(db, tmp_path / "semantic.npz")
    activation_kwargs = _activation_inputs(tmp_path, db, production, validator)

    manifest = production.activate_rag(db, semantic_index_path=sidecar, **activation_kwargs)
    assert manifest["semantic_index_sha256"] == production.sha256_file(sidecar)
    assert manifest["semantic_manifest_sha256"] == production.sha256_file(Path(str(sidecar) + ".manifest.json"))
    assert manifest["validation_evidence"]["semantic_index_sha256"] == manifest["semantic_index_sha256"]
    assert manifest["validation_evidence"]["semantic_manifest_sha256"] == manifest["semantic_manifest_sha256"]
    status = production.active_rag_status()
    assert status["active"] is True
    assert status["semantic"]["path"] == str(sidecar.resolve())
    assert status["semantic"]["index_sha256"] == manifest["semantic_index_sha256"]
    assert status["semantic"]["manifest_sha256"] == manifest["semantic_manifest_sha256"]


def test_frozen_activation_writes_user_overlay_and_preserves_bundled_manifest(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    bundled_manifest = tmp_path / "bundle" / "knowledge_base" / "active_rag.json"
    user_manifest = tmp_path / "user" / "knowledge_base" / "active_rag.json"
    bundled_manifest.parent.mkdir(parents=True)
    bundled_manifest.write_text(json.dumps({"database_path": "bundled.sqlite3", "database_sha256": "baseline"}), encoding="utf-8")
    bundled_before = bundled_manifest.read_bytes()
    candidate_root = user_manifest.parent / "production-rag-user"
    candidate_root.mkdir(parents=True)
    db = _indexed_database(candidate_root)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(production, "active_rag_manifest_path", lambda: user_manifest if user_manifest.is_file() else bundled_manifest)
    monkeypatch.setattr(production, "active_rag_write_manifest_path", lambda: user_manifest)
    monkeypatch.setattr(production, "active_rag_storage_scope", lambda: "user_data" if user_manifest.is_file() else "project")
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    activation_kwargs = _activation_inputs(tmp_path, db, production, validator)

    manifest = production.activate_rag(db, **activation_kwargs)

    assert user_manifest.is_file()
    assert bundled_manifest.read_bytes() == bundled_before
    assert manifest["previous"] == {}
    assert manifest["database_path"] == "production-rag-user/candidate.sqlite3"
    assert production.active_rag_manifest_path() == user_manifest


def test_frozen_rollback_removes_first_user_overlay_and_reveals_bundle(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production

    bundled_manifest = tmp_path / "bundle" / "knowledge_base" / "active_rag.json"
    user_manifest = tmp_path / "user" / "knowledge_base" / "active_rag.json"
    bundled_manifest.parent.mkdir(parents=True)
    bundled_payload = {"database_path": "bundled.sqlite3", "database_sha256": "baseline"}
    bundled_manifest.write_text(json.dumps(bundled_payload), encoding="utf-8")
    user_manifest.parent.mkdir(parents=True)
    user_manifest.write_text(json.dumps({"database_path": "user.sqlite3", "previous": {}}), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(production, "active_rag_manifest_path", lambda: user_manifest if user_manifest.is_file() else bundled_manifest)
    monkeypatch.setattr(production, "active_rag_write_manifest_path", lambda: user_manifest)
    monkeypatch.setattr(production, "active_rag_storage_scope", lambda: "user_data" if user_manifest.is_file() else "project")

    result = production.rollback_rag()

    assert result["rolled_back"] is True
    assert not user_manifest.exists()
    assert result["manifest"] == bundled_payload
    assert production.active_rag_manifest_path() == bundled_manifest


def test_frozen_rollback_is_non_destructive_when_only_bundle_is_active(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production

    bundled_manifest = tmp_path / "bundle" / "knowledge_base" / "active_rag.json"
    user_manifest = tmp_path / "user" / "knowledge_base" / "active_rag.json"
    bundled_manifest.parent.mkdir(parents=True)
    bundled_manifest.write_text(json.dumps({"database_path": "bundled.sqlite3"}), encoding="utf-8")
    bundled_before = bundled_manifest.read_bytes()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(production, "active_rag_manifest_path", lambda: bundled_manifest)
    monkeypatch.setattr(production, "active_rag_write_manifest_path", lambda: user_manifest)

    result = production.rollback_rag()

    assert result["rolled_back"] is False
    assert result["reason"] == "bundled_baseline_active"
    assert bundled_manifest.read_bytes() == bundled_before
    assert not user_manifest.exists()


def test_activation_blocks_semantic_replacement_after_replay(tmp_path: Path, monkeypatch) -> None:
    import numpy as np
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    db = _indexed_database(tmp_path)
    sidecar = _semantic_sidecar(db, tmp_path / "semantic.npz")
    activation_kwargs = _activation_inputs(tmp_path, db, production, validator)
    active_manifest = production.active_rag_manifest_path()
    active_manifest_before = active_manifest.read_bytes() if active_manifest.is_file() else None
    original_validate = validator.validate

    def replacing_validate(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        with np.load(sidecar, allow_pickle=False) as arrays:
            vectors = np.asarray(arrays["vectors"]) * 2
            chunk_ids = np.asarray(arrays["chunk_ids"])
            content_hashes = np.asarray(arrays["content_hashes"])
        np.savez_compressed(sidecar, vectors=vectors, chunk_ids=chunk_ids, content_hashes=content_hashes)
        return result

    monkeypatch.setattr(validator, "validate", replacing_validate)
    try:
        production.activate_rag(db, semantic_index_path=sidecar, **activation_kwargs)
    except ValueError as exc:
        assert "最终摘要" in str(exc)
    else:
        raise AssertionError("semantic replacement after replay must block activation")
    assert (active_manifest.read_bytes() if active_manifest.is_file() else None) == active_manifest_before


def test_build_groups_duplicate_content_and_records_aliases(tmp_path: Path, monkeypatch) -> None:
    import scripts.build_production_rag as builder

    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "a.pdf").write_bytes(b"same-pdf")
    (source_dir / "b.pdf").write_bytes(b"same-pdf")
    output_dir = tmp_path / "candidate"
    calls: list[Path] = []

    quality = SimpleNamespace(to_dict=lambda: {
        "page_count": 1, "failed_pages": [], "low_quality_pages": [], "warnings": [],
        "quality_score": 0.9, "needs_review": False, "ocr_pages": 0,
    })
    status = SimpleNamespace(status="success_with_warnings", error="", warnings=["review recommended"])
    page = SimpleNamespace(to_dict=lambda: {"page_number": 1, "native_text_chars": 20, "ocr_text_chars": 0, "warnings": []})
    converted = SimpleNamespace(
        document_id="doc-a",
        quality_report=quality,
        status=status,
        pages=[page],
        metadata={"backend": "docling+pymupdf"},
        to_dict=lambda: {"document_id": "doc-a"},
    )

    def fake_convert(path: Path):
        calls.append(path)
        return converted

    monkeypatch.setattr(builder, "convert_document", fake_convert)
    monkeypatch.setattr(builder, "chunk_conversion", lambda value: {"document_id": "doc-a", "chunks": []})
    monkeypatch.setattr(builder, "build_index", lambda payload, db: {"status": "ready", "chunk_count": 1, "retrieval_chunk_count": 1})
    monkeypatch.setattr(builder, "inspect_v2_database", lambda db, strict=True: {
        "healthy": True, "schema_compatible": True, "integrity_healthy": True, "metadata_healthy": True,
        "documents": 1,
    })
    result = builder.build(source_dir, output_dir)
    assert len(calls) == 1
    assert result["source_file_count"] == 2
    assert result["canonical_document_count"] == 1
    assert result["duplicate_alias_count"] == 1
    assert len(result["documents"][0]["aliases"]) == 2
    assert result["active_manifest_touched"] is False


def test_remote_blank_review_rejects_cached_conversion_with_failed_pages(tmp_path: Path) -> None:
    import scripts.build_production_rag as builder
    from knowledge_pipeline.contracts import DocumentConversion, PageRecord, QualityReport, StageStatus

    source = tmp_path / "blank.pdf"
    source.write_bytes(b"blank-pdf")
    digest = builder.sha256_file(source)
    document_id = hashlib.sha256(f"{source.resolve()}:{digest}".encode()).hexdigest()[:20]
    conversion = DocumentConversion(
        document_id=document_id,
        source_path=str(source.resolve()),
        source_name=source.name,
        source_sha256=digest,
        extension=".pdf",
        pages=[PageRecord(page_number=1, page_type="blank_or_unreadable", needs_review=True)],
        quality_report=QualityReport(page_count=1, failed_pages=[1], low_quality_pages=[1]),
        status=StageStatus(status="success_with_warnings", stage_version=builder.REQUIRED_PDF_STAGE_VERSION),
        metadata={"backend": "docling+pymupdf"},
    )
    cache_dir = tmp_path / "converted"
    cache_dir.mkdir()
    (cache_dir / f"{digest[:20]}.conversion.json").write_text(
        json.dumps(conversion.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    assert builder._load_docling_conversion_cache(source, digest, [cache_dir]) is not None
    assert builder._load_docling_conversion_cache(
        source,
        digest,
        [cache_dir],
        remote_blank_review=True,
    ) is None


def test_build_does_not_activate_when_required_gate_fails(tmp_path: Path, monkeypatch) -> None:
    import scripts.build_production_rag as builder

    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "bad.pdf").write_bytes(b"bad-pdf")
    quality = SimpleNamespace(to_dict=lambda: {
        "page_count": 1, "failed_pages": [1], "low_quality_pages": [1], "warnings": ["OCR failed"],
        "quality_score": 0.2, "needs_review": True, "ocr_pages": 1,
    })
    status = SimpleNamespace(status="ready", error="", warnings=["OCR failed"])
    page = SimpleNamespace(to_dict=lambda: {"page_number": 1, "native_text_chars": 0, "ocr_text_chars": 0, "warnings": ["OCR failed"]})
    converted = SimpleNamespace(
        document_id="doc-bad",
        quality_report=quality,
        status=status,
        pages=[page],
        metadata={"backend": "docling+pymupdf"},
        to_dict=lambda: {"document_id": "doc-bad"},
    )
    activated: list[bool] = []
    monkeypatch.setattr(builder, "convert_document", lambda path: converted)
    monkeypatch.setattr(builder, "chunk_conversion", lambda value: {"document_id": "doc-bad", "chunks": []})
    monkeypatch.setattr(builder, "build_index", lambda payload, db: {"status": "ready", "chunk_count": 1, "retrieval_chunk_count": 1})
    monkeypatch.setattr(builder, "inspect_v2_database", lambda db, strict=True: {
        "healthy": True, "schema_compatible": True, "integrity_healthy": True, "metadata_healthy": True,
        "documents": 1,
    })
    monkeypatch.setattr(builder, "activate_rag", lambda *args, **kwargs: activated.append(True))
    legacy = tmp_path / "legacy.sqlite3"
    legacy.write_bytes(b"placeholder")
    queries = tmp_path / "queries.json"
    scopes = tmp_path / "scopes.json"
    queries.write_text(json.dumps([{"query": "bad", "expected_document_ids": ["doc-bad"]}]), encoding="utf-8")
    scopes.write_text(json.dumps([{"document_ids": ["doc-bad"]}]), encoding="utf-8")
    result = builder.build(
        source_dir, tmp_path / "candidate", activate=True,
        query_expectations_path=queries, legacy_db=legacy, scope_expectations_path=scopes,
    )
    assert result["status"] == "failed"
    activation = json.loads((tmp_path / "candidate" / "activation_result.json").read_text(encoding="utf-8"))
    assert activation["reason"] == "required_gate_failed"
    assert "activation" not in result
    assert activated == []


def test_build_manifest_is_not_rewritten_after_activation_attempt(tmp_path: Path, monkeypatch) -> None:
    import scripts.build_production_rag as builder

    source_dir = tmp_path / "sources"; source_dir.mkdir(); (source_dir / "bad.pdf").write_bytes(b"bad")
    quality = SimpleNamespace(to_dict=lambda: {"page_count": 1, "failed_pages": [1], "low_quality_pages": [], "warnings": [], "quality_score": 0.9, "needs_review": True, "ocr_pages": 1})
    status = SimpleNamespace(status="ready", error="", warnings=[])
    page = SimpleNamespace(to_dict=lambda: {"page_number": 1, "native_text_chars": 0, "ocr_text_chars": 0, "warnings": []})
    converted = SimpleNamespace(
        document_id="doc",
        quality_report=quality,
        status=status,
        pages=[page],
        metadata={"backend": "docling+pymupdf"},
        to_dict=lambda: {"document_id": "doc"},
    )
    monkeypatch.setattr(builder, "convert_document", lambda path: converted)
    monkeypatch.setattr(builder, "chunk_conversion", lambda value: {"document_id": "doc", "chunks": []})
    monkeypatch.setattr(builder, "build_index", lambda payload, db: {"status": "ready", "chunk_count": 1, "retrieval_chunk_count": 1})
    monkeypatch.setattr(builder, "inspect_v2_database", lambda db, strict=True: {"healthy": True, "schema_compatible": True, "integrity_healthy": True, "metadata_healthy": True, "documents": 1})
    legacy = tmp_path / "legacy.sqlite3"; legacy.write_bytes(b"legacy")
    query = tmp_path / "q.json"; query.write_text(json.dumps([{"query": "q", "expected_document_ids": ["doc"]}]), encoding="utf-8")
    scope = tmp_path / "s.json"; scope.write_text(json.dumps([{"document_ids": ["doc"]}]), encoding="utf-8")
    output = tmp_path / "candidate"
    result = builder.build(source_dir, output, activate=True, query_expectations_path=query, legacy_db=legacy, scope_expectations_path=scope)
    on_disk = json.loads((output / "build_manifest.json").read_text(encoding="utf-8"))
    assert "activation" not in on_disk and "activation_result" not in on_disk
    assert result["activation_result"]["activated"] is False
    assert (output / "activation_result.json").is_file()


def test_validation_fails_wrong_expected_document_or_standard(tmp_path: Path, monkeypatch) -> None:
    import scripts.validate_production_rag as validator

    db = _indexed_database(tmp_path)
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False, "reason": "test"})
    expectations = tmp_path / "queries.json"
    expectations.write_text(json.dumps([{"query": "GB 55021-2021", "expected_document_ids": ["other"], "expected_standards": ["GB 50010-2010"]}]), encoding="utf-8")
    report = validator.validate(
        db,
        tmp_path / "validation.json",
        query_expectations_path=expectations,
    )
    assert report["status"] == "failed"
    assert report["queries"][0]["passed"] is False
    assert "expected_document_missing" in report["queries"][0]["failures"]
    assert "expected_standard_missing" in report["queries"][0]["failures"]


def test_validation_rejects_query_without_relevance_assertion(tmp_path: Path, monkeypatch) -> None:
    import scripts.validate_production_rag as validator

    db = _indexed_database(tmp_path)
    path = tmp_path / "queries.json"
    path.write_text(json.dumps([{"query": "GB 55021-2021"}]), encoding="utf-8")
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    report = validator.validate(db, tmp_path / "validation.json", query_expectations_path=path)
    assert report["status"] == "failed"
    assert "query_expectation_missing_assertion:0" in report["configuration_failures"]


def test_build_rejects_nonempty_active_or_legacy_output_and_preserves_bytes(tmp_path: Path, monkeypatch) -> None:
    import scripts.build_production_rag as builder

    data_root = tmp_path / "user-data"
    knowledge = data_root / "knowledge_base"
    active_dir = knowledge / "production-current"
    active_dir.mkdir(parents=True)
    active_db = active_dir / "knowledge_base_v2.sqlite3"
    active_db.write_bytes(b"active-db-bytes")
    legacy_db = knowledge / "knowledge_base.sqlite3"
    legacy_db.write_bytes(b"legacy-db-bytes")
    manifest = knowledge / "active_rag.json"
    manifest.write_text(json.dumps({"database_path": str(active_db), "database_sha256": "placeholder"}), encoding="utf-8")
    manifest_before = hashlib.sha256(manifest.read_bytes()).hexdigest()
    active_before = hashlib.sha256(active_db.read_bytes()).hexdigest()
    legacy_before = hashlib.sha256(legacy_db.read_bytes()).hexdigest()
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(data_root))

    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "a.pdf").write_bytes(b"pdf")
    for output in (active_dir, knowledge, tmp_path / "nonempty"):
        if output.name == "nonempty":
            output.mkdir()
            (output / "keep.txt").write_text("keep", encoding="utf-8")
        try:
            builder.build(source_dir, output)
        except (ValueError, FileExistsError):
            pass
        else:
            raise AssertionError("protected or non-empty output must be rejected")
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == manifest_before
    assert hashlib.sha256(active_db.read_bytes()).hexdigest() == active_before
    assert hashlib.sha256(legacy_db.read_bytes()).hexdigest() == legacy_before


def test_activate_requires_replayable_validation_report_bound_to_candidate(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    data_root = tmp_path / "data"
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(data_root))
    db = _indexed_database(tmp_path)
    try:
        production.activate_rag(db)  # type: ignore[call-arg]
    except ValueError as exc:
        assert "validation_report_path" in str(exc)
    else:
        raise AssertionError("activation without validation evidence must fail")
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.executescript("""
        CREATE TABLE documents(document_id TEXT PRIMARY KEY,status TEXT,folder_id TEXT);
        CREATE TABLE folders(folder_id TEXT PRIMARY KEY,parent_id TEXT);
        CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,document_id TEXT);
        INSERT INTO folders VALUES ('root','');
        INSERT INTO documents VALUES ('doc-quality','ready','root');
        INSERT INTO chunks VALUES ('c','doc-quality');
        """)
    build_manifest = tmp_path / "build_manifest.json"
    health = production.inspect_v2_database(db, strict=True)
    build_manifest.write_text(json.dumps({"database": {"sha256": health["sha256"]}, "required_gate": {"passed": True}, "canonical_document_count": 1}), encoding="utf-8")
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    validation_path = tmp_path / "validation.json"
    query_path = tmp_path / "queries.json"
    scope_path = tmp_path / "scopes.json"
    query_path.write_text(json.dumps([{"query": "GB 55021-2021", "expected_standards": ["GB 55021-2021"]}]), encoding="utf-8")
    scope_path.write_text(json.dumps([{"folder_ids": ["root"]}]), encoding="utf-8")
    validator.validate(
        db, validation_path, query_expectations_path=query_path,
        build_manifest=build_manifest, legacy_db=legacy, scope_expectations_path=scope_path,
    )
    activation_kwargs = {
        "validation_report_path": validation_path,
        "query_expectations_path": query_path,
        "scope_expectations_path": scope_path,
        "build_manifest_path": build_manifest,
        "legacy_db_path": legacy,
        "require_v2_scope": False,
    }
    manifest = production.activate_rag(db, **activation_kwargs)
    assert manifest["database_sha256"] == health["sha256"]
    assert production.active_rag_manifest_path().is_file()
    forged = json.loads(validation_path.read_text(encoding="utf-8"))
    query_path.write_text(json.dumps([{"query": "完全不存在", "expected_document_ids": ["missing"]}]), encoding="utf-8")
    forged["validation_evidence_hash"] = validator._canonical_hash(forged)
    validation_path.write_text(json.dumps(forged), encoding="utf-8")
    try:
        production.activate_rag(db, **activation_kwargs)
    except ValueError as exc:
        assert "build manifest" in str(exc)
    else:
        raise AssertionError("recomputed hash must not bypass validation replay")


def test_activate_rejects_report_rebound_to_easier_files(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    db = _indexed_database(tmp_path)
    health = production.inspect_v2_database(db, strict=True)
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.executescript("""
        CREATE TABLE documents(document_id TEXT PRIMARY KEY,status TEXT,folder_id TEXT);
        CREATE TABLE folders(folder_id TEXT PRIMARY KEY,parent_id TEXT);
        CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,document_id TEXT);
        INSERT INTO folders VALUES ('root','');
        INSERT INTO documents VALUES ('doc-quality','ready','root');
        INSERT INTO chunks VALUES ('c','doc-quality');
        """)
    original_query = tmp_path / "original-query.json"
    original_scope = tmp_path / "original-scope.json"
    original_build = tmp_path / "original-build.json"
    original_query.write_text(json.dumps([{"query": "GB 55021-2021", "expected_standards": ["GB 55021-2021"]}]), encoding="utf-8")
    original_scope.write_text(json.dumps([{"folder_ids": ["root"]}]), encoding="utf-8")
    original_build.write_text(json.dumps({"database": {"sha256": health["sha256"]}, "required_gate": {"passed": True}, "canonical_document_count": 1}), encoding="utf-8")
    report_path = tmp_path / "validation.json"
    validator.validate(db, report_path, query_expectations_path=original_query, build_manifest=original_build, legacy_db=legacy, scope_expectations_path=original_scope)

    easier_query = tmp_path / "easy-query.json"
    easier_scope = tmp_path / "easy-scope.json"
    easier_build = tmp_path / "easy-build.json"
    easier_query.write_text(original_query.read_text(encoding="utf-8"), encoding="utf-8")
    easier_scope.write_text(original_scope.read_text(encoding="utf-8"), encoding="utf-8")
    easier_build.write_text(original_build.read_text(encoding="utf-8"), encoding="utf-8")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for key, path in (("query_expectations_file", easier_query), ("scope_expectations_file", easier_scope), ("build_manifest_file", easier_build)):
        report["validation_inputs"][key]["path"] = str(path.resolve())
        report["validation_inputs"][key]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["validation_evidence_hash"] = validator._canonical_hash(report)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    try:
        production.activate_rag(
            db, validation_report_path=report_path,
            query_expectations_path=original_query, scope_expectations_path=original_scope,
            build_manifest_path=original_build, legacy_db_path=legacy, require_v2_scope=False,
        )
    except ValueError as exc:
        assert "不一致" in str(exc)
    else:
        raise AssertionError("report-rebound inputs must not override authoritative activation parameters")


def test_activate_rejects_explicit_path_different_from_report(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    db = _indexed_database(tmp_path)
    health = production.inspect_v2_database(db, strict=True)
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.executescript("CREATE TABLE documents(document_id TEXT,status TEXT,folder_id TEXT); CREATE TABLE folders(folder_id TEXT,parent_id TEXT); CREATE TABLE chunks(chunk_id TEXT,document_id TEXT); INSERT INTO folders VALUES ('root',''); INSERT INTO documents VALUES ('doc-quality','ready','root'); INSERT INTO chunks VALUES ('c','doc-quality');")
    query = tmp_path / "query.json"; scope = tmp_path / "scope.json"; build = tmp_path / "build.json"
    query.write_text(json.dumps([{"query": "GB 55021-2021", "expected_standards": ["GB 55021-2021"]}]), encoding="utf-8")
    scope.write_text(json.dumps([{"folder_ids": ["root"]}]), encoding="utf-8")
    build.write_text(json.dumps({"database": {"sha256": health["sha256"]}, "required_gate": {"passed": True}, "canonical_document_count": 1}), encoding="utf-8")
    report = tmp_path / "validation.json"
    validator.validate(db, report, query_expectations_path=query, build_manifest=build, legacy_db=legacy, scope_expectations_path=scope)
    other_query = tmp_path / "other-query.json"
    other_query.write_text(query.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        production.activate_rag(db, validation_report_path=report, query_expectations_path=other_query, scope_expectations_path=scope, build_manifest_path=build, legacy_db_path=legacy, require_v2_scope=False)
    except ValueError as exc:
        assert "不一致" in str(exc)
    else:
        raise AssertionError("explicit paths must match report bindings exactly")


def test_build_activate_requires_gui_scope_inputs(tmp_path: Path) -> None:
    import scripts.build_production_rag as builder

    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    try:
        builder.build(source_dir, tmp_path / "candidate", activate=True)
    except ValueError as exc:
        assert "--legacy-db" in str(exc)
    else:
        raise AssertionError("--activate without GUI scope validation inputs must fail")


def test_semantic_sidecar_validates_npz_shape_and_identity(tmp_path: Path) -> None:
    import json as json_module
    import numpy as np
    from runtime.rag_production import inspect_v2_database

    db = _indexed_database(tmp_path)
    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT chunk_id,content_hash FROM pipeline_chunks WHERE retrieval_role='retrieval' AND text_search<>'' ORDER BY document_id,location,chunk_id"
        ).fetchall()
    identity = [(str(row[0]), str(row[1])) for row in rows]
    fingerprint = hashlib.sha256(json_module.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    sidecar = tmp_path / "semantic.npz"
    np.savez_compressed(sidecar, vectors=np.ones((len(identity), 3), dtype=np.float32), chunk_ids=np.asarray([item[0] for item in identity]), content_hashes=np.asarray([item[1] for item in identity]))
    Path(str(sidecar) + ".manifest.json").write_text(json.dumps({"schema_version": "knowledge-embeddings.v1", "count": len(identity), "dimension": 3, "source_fingerprint": fingerprint}), encoding="utf-8")
    assert inspect_v2_database(db, semantic_index_path=sidecar, strict=True)["healthy"] is True
    np.savez_compressed(sidecar, vectors=np.ones((len(identity), 3, 1), dtype=np.float32), chunk_ids=np.asarray([item[0] for item in identity]), content_hashes=np.asarray([item[1] for item in identity]))
    broken = inspect_v2_database(db, semantic_index_path=sidecar, strict=True)
    assert broken["healthy"] is False
    assert any(reason in broken["integrity_errors"] for reason in ("semantic_array_shape_invalid", "semantic_index_shape_invalid"))
    np.savez_compressed(sidecar, vectors=np.ones((len(identity), 3), dtype=np.float32), chunk_ids=np.asarray([item[0] for item in identity], dtype=object), content_hashes=np.asarray([item[1] for item in identity], dtype=object))
    unsafe = inspect_v2_database(db, semantic_index_path=sidecar, strict=True)
    assert unsafe["healthy"] is False
    assert any(reason in unsafe["integrity_errors"] for reason in ("semantic_object_dtype_unsafe", "semantic_index_object_dtype_unsafe"))


def test_version_compatibility_accepts_newer_and_rejects_older(tmp_path: Path) -> None:
    from runtime.rag_production import inspect_v2_database

    db = _indexed_database(tmp_path)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE pipeline_documents SET index_version='knowledge-index.v2.1'")
        payload = json.loads(connection.execute("SELECT payload_json FROM pipeline_index_manifest").fetchone()[0])
        payload["stage_version"] = "index.v2.3"
        connection.execute("UPDATE pipeline_index_manifest SET payload_json=?", (json.dumps(payload),))
        connection.commit()
    assert inspect_v2_database(db, strict=True)["healthy"] is True
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE pipeline_documents SET index_version='knowledge-index.v1.9'")
        connection.commit()
    result = inspect_v2_database(db, strict=True)
    assert result["healthy"] is False
    assert any(item.get("reason") == "document_index_version_incompatible" for item in result["schema_errors"] if isinstance(item, dict))


def test_page_inventory_requires_exact_contiguous_page_numbers(tmp_path: Path) -> None:
    from runtime.rag_production import inspect_v2_database

    db = _indexed_database(tmp_path)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE pipeline_pages SET page_number=2")
        connection.commit()
    result = inspect_v2_database(db, strict=True)
    assert result["healthy"] is False
    assert "document_page_coverage_incomplete:doc-quality" in result["quality_errors"]


def test_severe_warnings_merge_sources_and_normalize_spacing(tmp_path: Path) -> None:
    from runtime.rag_production import inspect_v2_database

    db = _indexed_database(tmp_path)
    with closing(sqlite3.connect(db)) as connection:
        row = connection.execute("SELECT metadata_json FROM pipeline_documents").fetchone()
        metadata = json.loads(row[0])
        metadata["quality_report"]["warnings"] = ["普通表格需人工复核"]
        metadata["conversion_warnings"] = ["第 7 页：页面没有可用原生文本 或 OCR 文本", "第8页：O C R 失 败"]
        connection.execute("UPDATE pipeline_documents SET metadata_json=?", (json.dumps(metadata, ensure_ascii=False),))
        connection.commit()
    result = inspect_v2_database(db, strict=True)
    quality = result["document_quality"][0]
    assert "普通表格需人工复核" in quality["warnings"]
    assert len(quality["severe_warnings"]) == 2
    assert "document_severe_conversion_warning:doc-quality" in result["quality_errors"]


def test_health_fails_closed_for_corrupt_nan_and_infinite_numeric_metadata(tmp_path: Path) -> None:
    from runtime.rag_production import inspect_v2_database

    for index, value in enumerate(("broken", float("nan"), float("inf"))):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        db = _indexed_database(case)
        with closing(sqlite3.connect(db)) as connection:
            connection.execute("UPDATE pipeline_documents SET quality_score=?", (value,))
            connection.commit()
        result = inspect_v2_database(db, strict=True)
        assert result["healthy"] is False
        assert result["reason"] in {"quality_metadata_incomplete", "database_error:ValueError", "database_error:TypeError", "database_error:OverflowError"}


def test_validate_malformed_build_manifest_returns_failed_report(tmp_path: Path, monkeypatch) -> None:
    import scripts.validate_production_rag as validator

    db = _indexed_database(tmp_path)
    query = tmp_path / "query.json"
    query.write_text(json.dumps([{"query": "GB 55021-2021", "expected_standards": ["GB 55021-2021"]}]), encoding="utf-8")
    malformed = tmp_path / "build.json"
    malformed.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    report = validator.validate(db, tmp_path / "report.json", query_expectations_path=query, build_manifest=malformed)
    assert report["status"] == "failed"
    assert "build_manifest_invalid" in report["configuration_failures"]


def test_portable_relative_path_cannot_escape_knowledge_root(tmp_path: Path, monkeypatch) -> None:
    from runtime.app_paths import resolve_knowledge_portable_path

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "data"))
    assert resolve_knowledge_portable_path("../escape.sqlite3") is None
    assert resolve_knowledge_portable_path("C:relative.sqlite3") is None
    assert resolve_knowledge_portable_path(r"\\server\share\db.sqlite3") is None
    valid = resolve_knowledge_portable_path("production-v3/knowledge_base_v2.sqlite3")
    assert valid is not None
    assert str(valid).startswith(str((tmp_path / "data" / "knowledge_base").resolve()))


def test_activate_detects_same_path_replacement_after_validation_rerun(tmp_path: Path, monkeypatch) -> None:
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    db = _indexed_database(tmp_path)
    health = production.inspect_v2_database(db, strict=True)
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.executescript("CREATE TABLE documents(document_id TEXT,status TEXT,folder_id TEXT); CREATE TABLE folders(folder_id TEXT,parent_id TEXT); CREATE TABLE chunks(chunk_id TEXT,document_id TEXT); INSERT INTO folders VALUES ('root',''); INSERT INTO documents VALUES ('doc-quality','ready','root'); INSERT INTO chunks VALUES ('c','doc-quality');")
    query = tmp_path / "query.json"; scope = tmp_path / "scope.json"; build = tmp_path / "build.json"
    query.write_text(json.dumps([{"query": "GB 55021-2021", "expected_standards": ["GB 55021-2021"]}]), encoding="utf-8")
    scope.write_text(json.dumps([{"folder_ids": ["root"]}]), encoding="utf-8")
    build.write_text(json.dumps({"database": {"sha256": health["sha256"]}, "required_gate": {"passed": True}, "canonical_document_count": 1}), encoding="utf-8")
    report = tmp_path / "validation.json"
    validator.validate(db, report, query_expectations_path=query, build_manifest=build, legacy_db=legacy, scope_expectations_path=scope)
    original_validate = validator.validate

    def replacing_validate(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        query.write_text(json.dumps([{"query": "changed", "expected_document_ids": ["missing"]}]), encoding="utf-8")
        return result

    monkeypatch.setattr(validator, "validate", replacing_validate)
    try:
        production.activate_rag(db, validation_report_path=report, query_expectations_path=query, scope_expectations_path=scope, build_manifest_path=build, legacy_db_path=legacy, require_v2_scope=False)
    except ValueError as exc:
        assert "最终摘要" in str(exc)
    else:
        raise AssertionError("same-path replacement during replay must block activation")


def test_activate_detects_query_aba_replace_copy_restore(tmp_path: Path, monkeypatch) -> None:
    import shutil as shutil_module
    import runtime.rag_production as production
    import scripts.validate_production_rag as validator

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    db = _indexed_database(tmp_path)
    health = production.inspect_v2_database(db, strict=True)
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.executescript("CREATE TABLE documents(document_id TEXT,status TEXT,folder_id TEXT); CREATE TABLE folders(folder_id TEXT,parent_id TEXT); CREATE TABLE chunks(chunk_id TEXT,document_id TEXT); INSERT INTO folders VALUES ('root',''); INSERT INTO documents VALUES ('doc-quality','ready','root'); INSERT INTO chunks VALUES ('c','doc-quality');")
    query = tmp_path / "query.json"; scope = tmp_path / "scope.json"; build = tmp_path / "build.json"
    query.write_text(json.dumps([{"query": "GB 55021-2021", "expected_standards": ["GB 55021-2021"]}]), encoding="utf-8")
    scope.write_text(json.dumps([{"folder_ids": ["root"]}]), encoding="utf-8")
    build.write_text(json.dumps({"database": {"sha256": health["sha256"]}, "required_gate": {"passed": True}, "canonical_document_count": 1}), encoding="utf-8")
    report = tmp_path / "validation.json"
    validator.validate(db, report, query_expectations_path=query, build_manifest=build, legacy_db=legacy, scope_expectations_path=scope)
    real_copyfile = shutil_module.copyfile

    def aba_copyfile(source, target, *args, **kwargs):
        source_path = Path(source)
        if source_path.resolve() == query.resolve():
            original = source_path.read_bytes()
            source_path.write_text(json.dumps([{"query": "evil", "expected_document_ids": ["missing"]}]), encoding="utf-8")
            try:
                return real_copyfile(source, target, *args, **kwargs)
            finally:
                source_path.write_bytes(original)
        return real_copyfile(source, target, *args, **kwargs)

    monkeypatch.setattr(production.shutil, "copyfile", aba_copyfile)
    try:
        production.activate_rag(db, validation_report_path=report, query_expectations_path=query, scope_expectations_path=scope, build_manifest_path=build, legacy_db_path=legacy, require_v2_scope=False)
    except ValueError as exc:
        assert "query 快照摘要" in str(exc)
    else:
        raise AssertionError("ABA replace-copy-restore must be detected by snapshot digest")


def test_exclusive_lock_recovers_confirmed_stale_dead_pid(tmp_path: Path, monkeypatch) -> None:
    from runtime.rag_production import _ExclusiveFileLock

    lock_path = tmp_path / "stale.lock"
    lock_path.write_text("pid=999999 utc=2000-01-01T00:00:00+00:00", encoding="utf-8")
    monkeypatch.setattr(_ExclusiveFileLock, "_pid_alive", staticmethod(lambda pid: False))
    with _ExclusiveFileLock(lock_path, timeout_seconds=0.2):
        assert lock_path.is_file()
        assert "pid=" in lock_path.read_text(encoding="utf-8")
    assert not lock_path.exists()


def test_exclusive_lock_never_steals_live_pid(tmp_path: Path, monkeypatch) -> None:
    from runtime.rag_production import _ExclusiveFileLock

    lock_path = tmp_path / "active.lock"
    original = "pid=1234 utc=2000-01-01T00:00:00+00:00"
    lock_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(_ExclusiveFileLock, "_pid_alive", staticmethod(lambda pid: True))
    try:
        with _ExclusiveFileLock(lock_path, timeout_seconds=0.05):
            raise AssertionError("live lock must not be acquired")
    except TimeoutError:
        pass
    assert lock_path.read_text(encoding="utf-8") == original


def test_scope_validation_preserves_selected_folder_and_reports_fallback(tmp_path: Path, monkeypatch) -> None:
    import scripts.validate_production_rag as validator

    db = _indexed_database(tmp_path, document_id="doc-v2")
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.executescript("""
        CREATE TABLE documents(document_id TEXT PRIMARY KEY,status TEXT,folder_id TEXT);
        CREATE TABLE folders(folder_id TEXT PRIMARY KEY,parent_id TEXT);
        CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,document_id TEXT);
        INSERT INTO folders VALUES ('root','');
        INSERT INTO folders VALUES ('child','root');
        INSERT INTO documents VALUES ('doc-v2','ready','root');
        INSERT INTO documents VALUES ('doc-legacy','ready','child');
        INSERT INTO chunks VALUES ('c-v2','doc-v2');
        INSERT INTO chunks VALUES ('c-legacy','doc-legacy');
        """)
    monkeypatch.setattr(validator, "active_rag_status", lambda: {"active": False})
    query_path = tmp_path / "queries.json"
    scope_path = tmp_path / "scopes.json"
    query_path.write_text(json.dumps([{"query": "GB 55021-2021", "expected_standards": ["GB 55021-2021"]}]), encoding="utf-8")
    scope_path.write_text(json.dumps([{"name": "folder", "folder_ids": ["root"]}]), encoding="utf-8")
    report = validator.validate(
        db, tmp_path / "scope.json",
        query_expectations_path=query_path,
        legacy_db=legacy, scope_expectations_path=scope_path,
    )
    scope = report["selected_scopes"][0]
    assert scope["resolved_ready_ids"] == ["doc-legacy", "doc-v2"]
    assert scope["v2_available"] == ["doc-v2"]
    assert scope["legacy_available"] == ["doc-legacy", "doc-v2"]
    assert scope["backend_plan"] == "v2_with_legacy_scope_fallback"
    assert scope["passed"] is True
