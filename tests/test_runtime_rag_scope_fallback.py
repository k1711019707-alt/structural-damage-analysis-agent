from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from knowledge_pipeline.index import build_index
from runtime.knowledge_base import ActiveRagScopeAdapter, KnowledgeBase, pipeline_search_with_scope


@pytest.fixture(autouse=True)
def _clear_runtime_rag_cache() -> None:
    from runtime.knowledge_base import _reset_rag_runtime_cache_for_tests

    _reset_rag_runtime_cache_for_tests()
    yield
    _reset_rag_runtime_cache_for_tests()


def _add_legacy_document(kb: KnowledgeBase, document_id: str, text: str, *, folder_id: str = "") -> None:
    with sqlite3.connect(kb.db_path) as db:
        db.execute(
            """INSERT INTO documents(
                   document_id, path, name, extension, size_bytes, modified_ns,
                   sha256, status, folder_id, error
               ) VALUES (?, ?, ?, '.pdf', 1, 1, ?, 'ready', ?, '')""",
            (document_id, f"{document_id}.pdf", f"{document_id}.pdf", document_id * 2, folder_id),
        )
        chunk_id = f"legacy:{document_id}"
        db.execute(
            "INSERT INTO chunks(chunk_id, document_id, location, text) VALUES (?, ?, 'page:1', ?)",
            (chunk_id, document_id, text),
        )
        db.execute(
            "INSERT INTO chunks_fts(chunk_id, document_id, location, text) VALUES (?, ?, 'page:1', ?)",
            (chunk_id, document_id, text),
        )


def _build_v2(db_path: Path, documents: dict[str, str], *, source_names: dict[str, str] | None = None) -> None:
    for index, (document_id, text) in enumerate(documents.items()):
        parent_id = f"{document_id}:parent"
        build_index(
            {
                "schema_version": "knowledge-chunks.v2",
                "document_id": document_id,
                "source_sha256": f"sha-{document_id}",
                "metadata": {
                    "source_name": (source_names or {}).get(document_id, f"{document_id}.pdf"),
                    "source_path": f"C:/{document_id}.pdf",
                    "quality_score": 1.0,
                },
                "chunks": [
                    {
                        "chunk_id": parent_id,
                        "location": "unit:0",
                        "text": f"{text}父上下文",
                        "parent_id": parent_id,
                        "source_marker": f"[KB:{document_id}:page:1]",
                        "metadata": {
                            "chunk_level": "parent",
                            "retrieval_role": "context_only",
                            "heading_path": ["第一章", "1.1 裂缝"],
                            "page_numbers": [1],
                            "text_search": f"{text}父上下文",
                        },
                    },
                    {
                        "chunk_id": f"{document_id}:child",
                        "location": "unit:0:part:0",
                        "text": text,
                        "parent_id": parent_id,
                        "source_marker": f"[KB:{document_id}:page:1]",
                        "metadata": {
                            "chunk_level": "child",
                            "retrieval_role": "retrieval",
                            "heading_path": ["第一章", "1.1 裂缝"],
                            "page_numbers": [1],
                            "text_search": text,
                        },
                    },
                ],
            },
            db_path,
        )


def _activate_for_test(monkeypatch: pytest.MonkeyPatch, db_path: Path, *, semantic_path: str = "") -> None:
    from runtime import rag_production

    monkeypatch.setattr(
        rag_production,
        "active_rag_status",
        lambda: {
            "active": True,
            "database": {"path": str(db_path)},
            "manifest": {"semantic_index_path": semantic_path},
        },
    )


def test_v2_scope_preserves_hierarchical_metadata_and_does_not_broaden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-v2", "legacy 裂缝")
    _add_legacy_document(kb, "doc-unselected", "裂缝 未选文档")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-v2": "裂缝检测", "doc-unselected": "裂缝 未选 v2"})
    _activate_for_test(monkeypatch, v2_db)

    result = pipeline_search_with_scope(kb, "裂缝检测", document_ids=["doc-v2"], top_k=1)

    assert result.scope_document_ids == ("doc-v2",)
    assert result.retrieval_mode == "hierarchical"
    assert result.anchors and result.context_groups
    assert result.chunks[0].metadata["anchor"] is True
    assert result.chunks[0].source_marker == "[KB:doc-v2:page:1]"
    assert {chunk.document_id for chunk in result.chunks} == {"doc-v2"}


def test_gui_scope_adapter_uses_only_active_v2_with_adaptive_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-selected": "裂缝检测", "doc-unselected": "未选文档裂缝"})
    _activate_for_test(monkeypatch, v2_db)

    result = pipeline_search_with_scope(
        ActiveRagScopeAdapter(),
        "裂缝检测",
        document_ids=["doc-selected"],
        top_k=1,
    )

    assert result.scope_available is True
    assert result.scope_document_ids == ("doc-selected",)
    assert {chunk.document_id for chunk in result.chunks} == {"doc-selected"}
    assert result.route_diagnostics["routing_mode"] == "adaptive"
    assert not (tmp_path / "knowledge_base.sqlite3").exists()


def test_source_title_query_seeds_only_selected_document_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-selected", "正文不含源文件标题")
    _add_legacy_document(kb, "doc-unselected", "同样不含源文件标题")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(
        v2_db,
        {"doc-selected": "正文内容", "doc-unselected": "未选正文"},
        source_names={
            "doc-selected": "混凝土结构工程施工质量验收规范(1).pdf",
            "doc-unselected": "混凝土结构工程施工质量验收规范-其他.pdf",
        },
    )
    _activate_for_test(monkeypatch, v2_db)

    result = pipeline_search_with_scope(
        kb,
        "混凝土结构工程施工质量验收规范",
        document_ids=["doc-selected"],
        top_k=1,
    )

    assert result.chunks
    assert {chunk.document_id for chunk in result.chunks} == {"doc-selected"}


def test_partial_v2_scope_merges_only_missing_selected_ids_from_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-v2", "legacy v2 copy")
    _add_legacy_document(kb, "doc-missing", "植筋锚固 旧库证据")
    _add_legacy_document(kb, "doc-unselected", "植筋锚固 不应召回")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-v2": "植筋锚固 v2 证据", "doc-unselected": "植筋锚固 未选 v2"})
    _activate_for_test(monkeypatch, v2_db)

    with pytest.raises(RuntimeError, match="unavailable"):
        pipeline_search_with_scope(kb, "植筋锚固", document_ids=["doc-v2", "doc-missing"], top_k=2)


def test_partial_v2_scope_reserves_character_budget_for_legacy_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-v2", "legacy v2 copy")
    _add_legacy_document(kb, "doc-missing", "乙" * 80)
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-v2": "甲" * 80})
    _activate_for_test(monkeypatch, v2_db)

    with pytest.raises(RuntimeError, match="unavailable"):
        pipeline_search_with_scope(kb, "甲乙", document_ids=["doc-v2", "doc-missing"], top_k=2, max_chars=40)


def test_all_selected_ids_missing_from_v2_fall_back_to_same_legacy_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-selected", "施工质量验收")
    _add_legacy_document(kb, "doc-unselected", "施工质量验收 不应召回")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-other-v2": "施工质量验收 其他 v2"})
    _activate_for_test(monkeypatch, v2_db)

    with pytest.raises(RuntimeError, match="unavailable"):
        pipeline_search_with_scope(kb, "施工质量验收", document_ids=["doc-selected"])


def test_empty_selected_folder_does_not_turn_into_global_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = KnowledgeBase(tmp_path / "legacy")
    folder = kb.create_folder("空文件夹")
    _add_legacy_document(kb, "doc-unselected", "裂缝 不应召回")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-unselected": "裂缝 v2 不应召回"})
    _activate_for_test(monkeypatch, v2_db)

    result = pipeline_search_with_scope(kb, "裂缝", folder_ids=[folder.folder_id])

    assert result.scope_available is False
    assert result.retrieval_mode == "none"
    assert result.chunks == ()


def test_relative_semantic_path_is_resolved_below_user_knowledge_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "user-data"
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(data_root))
    knowledge_root = data_root / "knowledge_base"
    semantic_path = knowledge_root / "candidate" / "semantic.npz"
    semantic_path.parent.mkdir(parents=True)
    semantic_path.write_bytes(b"placeholder")
    (knowledge_root / "active_rag.json").write_text(
        json.dumps({"semantic_index_path": "candidate/semantic.npz"}),
        encoding="utf-8",
    )

    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-v2", "legacy 裂缝")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-v2": "裂缝检测"})
    _activate_for_test(monkeypatch, v2_db, semantic_path="candidate/semantic.npz")

    from knowledge_pipeline import semantic_retrieve

    captured: dict[str, Path] = {}

    class FakeSemanticRetriever:
        def __init__(self, path: str | Path) -> None:
            captured["path"] = Path(path)

        def search(self, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
            return []

    monkeypatch.setattr(semantic_retrieve, "SemanticRetriever", FakeSemanticRetriever)

    result = pipeline_search_with_scope(kb, "裂缝检测", document_ids=["doc-v2"])

    assert captured["path"] == semantic_path.resolve()
    assert result.scope_available is True


def test_semantic_loader_failure_keeps_lexical_v2_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "user-data"
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(data_root))
    knowledge_root = data_root / "knowledge_base"
    knowledge_root.mkdir(parents=True)
    (knowledge_root / "active_rag.json").write_text(
        json.dumps({"semantic_index_path": "missing/semantic.npz"}),
        encoding="utf-8",
    )
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-v2", "legacy 裂缝")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-v2": "裂缝检测"})
    _activate_for_test(monkeypatch, v2_db, semantic_path="missing/semantic.npz")

    from knowledge_pipeline import semantic_retrieve

    class BrokenSemanticRetriever:
        def __init__(self, _path: str | Path) -> None:
            raise RuntimeError(r"sidecar missing: C:\Users\private-user\semantic.npz")

    monkeypatch.setattr(semantic_retrieve, "SemanticRetriever", BrokenSemanticRetriever)

    result = pipeline_search_with_scope(kb, "裂缝检测", document_ids=["doc-v2"])

    assert result.scope_available is True
    assert {chunk.document_id for chunk in result.chunks} == {"doc-v2"}
    assert "semantic_unavailable:RuntimeError" in result.warnings
    assert all("private-user" not in warning and "semantic.npz" not in warning for warning in result.warnings)


def test_active_status_is_revalidated_while_semantic_retriever_is_digest_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "user-data"
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(data_root))
    knowledge_root = data_root / "knowledge_base"
    semantic_path = knowledge_root / "candidate" / "semantic.npz"
    semantic_path.parent.mkdir(parents=True)
    semantic_path.write_bytes(b"semantic-v1")
    Path(str(semantic_path) + ".manifest.json").write_text("{}", encoding="utf-8")
    active_manifest = knowledge_root / "active_rag.json"
    active_manifest.write_text(
        json.dumps({"semantic_index_path": "candidate/semantic.npz"}), encoding="utf-8"
    )

    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-v2", "legacy 裂缝")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-v2": "裂缝检测"})

    from runtime import rag_production
    status_calls = 0

    def active_status() -> dict[str, object]:
        nonlocal status_calls
        status_calls += 1
        return {
            "active": True,
            "database": {"path": str(v2_db)},
            "manifest": {"semantic_index_path": "candidate/semantic.npz"},
        }

    monkeypatch.setattr(rag_production, "active_rag_status", active_status)
    from knowledge_pipeline import semantic_retrieve
    constructed: list[Path] = []

    class FakeSemanticRetriever:
        def __init__(self, path: str | Path) -> None:
            constructed.append(Path(path))

        def search(self, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
            return []

    monkeypatch.setattr(semantic_retrieve, "SemanticRetriever", FakeSemanticRetriever)

    first = pipeline_search_with_scope(kb, "裂缝检测", document_ids=["doc-v2"])
    second = pipeline_search_with_scope(kb, "裂缝检测", document_ids=["doc-v2"])

    assert first.chunks and second.chunks
    assert status_calls == 2
    assert constructed == [semantic_path.resolve()]

    semantic_path.write_bytes(b"semantic-v2-with-different-size")
    third = pipeline_search_with_scope(kb, "裂缝检测", document_ids=["doc-v2"])

    assert third.chunks
    assert status_calls == 3
    assert constructed == [semantic_path.resolve(), semantic_path.resolve()]


def test_active_manifest_change_invalidates_cached_status_without_scope_broadening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "user-data"
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(data_root))
    knowledge_root = data_root / "knowledge_base"
    knowledge_root.mkdir(parents=True)
    active_manifest = knowledge_root / "active_rag.json"
    active_manifest.write_text(json.dumps({"database_path": "candidate-a/db.sqlite3"}), encoding="utf-8")

    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-selected", "选中文档 施工验收")
    _add_legacy_document(kb, "doc-unselected", "未选文档 施工验收")
    v2_db = tmp_path / "v2.sqlite3"
    _build_v2(v2_db, {"doc-selected": "选中文档 施工验收", "doc-unselected": "未选文档 施工验收"})

    from runtime import rag_production
    status_calls = 0

    def active_status() -> dict[str, object]:
        nonlocal status_calls
        status_calls += 1
        return {"active": True, "database": {"path": str(v2_db)}, "manifest": {}}

    monkeypatch.setattr(rag_production, "active_rag_status", active_status)
    first = pipeline_search_with_scope(kb, "施工验收", document_ids=["doc-selected"])
    active_manifest.write_text(
        json.dumps({"database_path": "candidate-b/database-with-longer-name.sqlite3"}), encoding="utf-8"
    )
    second = pipeline_search_with_scope(kb, "施工验收", document_ids=["doc-selected"])

    assert status_calls == 2
    assert {chunk.document_id for chunk in first.chunks} == {"doc-selected"}
    assert {chunk.document_id for chunk in second.chunks} == {"doc-selected"}


def test_same_size_same_mtime_database_replacement_invalidates_runtime_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "user-data"
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(data_root))
    knowledge_root = data_root / "knowledge_base"
    knowledge_root.mkdir(parents=True)
    (knowledge_root / "active_rag.json").write_text(
        json.dumps({"database_path": "candidate/db.sqlite3", "database_sha256": "declared-db-sha"}),
        encoding="utf-8",
    )
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-v2", "legacy 裂缝")
    v2_db = tmp_path / "v2.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    _build_v2(v2_db, {"doc-v2": "裂缝检测"})
    replacement.write_bytes(v2_db.read_bytes())
    original_stat = v2_db.stat()

    from runtime import rag_production
    calls = 0

    def active_status() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "active": True,
            "database": {"path": str(v2_db)},
            "manifest": {"database_sha256": "declared-db-sha"},
        }

    monkeypatch.setattr(rag_production, "active_rag_status", active_status)
    first = pipeline_search_with_scope(kb, "裂缝检测", document_ids=["doc-v2"])
    os.replace(replacement, v2_db)
    os.utime(v2_db, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second = pipeline_search_with_scope(kb, "裂缝检测", document_ids=["doc-v2"])

    assert first.chunks and second.chunks
    assert v2_db.stat().st_size == original_stat.st_size
    assert v2_db.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert calls == 2


def test_same_file_in_place_mutation_cannot_reuse_cached_active_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "user-data"
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(data_root))
    knowledge_root = data_root / "knowledge_base"
    knowledge_root.mkdir(parents=True)
    (knowledge_root / "active_rag.json").write_text(
        json.dumps({"database_path": "candidate/db.sqlite3", "database_sha256": "declared-db-sha"}),
        encoding="utf-8",
    )
    database = tmp_path / "same-file.sqlite3"
    database.write_bytes(b"0123456789")
    original_stat = database.stat()

    from runtime import rag_production
    from runtime.knowledge_base import _active_runtime_state

    calls = 0

    def active_status() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "active": True,
            "database": {"path": str(database)},
            "manifest": {"database_sha256": "declared-db-sha"},
        }

    monkeypatch.setattr(rag_production, "active_rag_status", active_status)
    _active_runtime_state()
    with database.open("r+b") as stream:
        stream.seek(3)
        stream.write(b"X")
        stream.flush()
        os.fsync(stream.fileno())
    os.utime(database, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert database.stat().st_size == original_stat.st_size
    assert database.stat().st_mtime_ns == original_stat.st_mtime_ns
    _active_runtime_state()

    assert calls == 2


def test_active_status_exception_is_redacted_from_pipeline_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-selected", "施工验收")

    from runtime import rag_production

    def broken_status() -> dict[str, object]:
        raise OSError(r"permission denied: C:\Users\private-user\active_rag.json")

    monkeypatch.setattr(rag_production, "active_rag_status", broken_status)
    with pytest.raises(RuntimeError, match="unavailable"):
        pipeline_search_with_scope(kb, "施工验收", document_ids=["doc-selected"])


def test_frozen_stale_user_overlay_uses_bundled_scope_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    from runtime import knowledge_base as knowledge_base_module

    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-bundled", "旧目录内容")
    user_db = tmp_path / "user-v2.sqlite3"
    bundled_db = tmp_path / "bundled-v2.sqlite3"
    _build_v2(user_db, {"doc-old": "旧用户知识库"})
    _build_v2(bundled_db, {"doc-bundled": "混凝土裂缝修复"})

    bundle_root = tmp_path / "bundle" / "_internal"
    bundled_manifest = bundle_root / "knowledge_base" / "active_rag.json"
    bundled_manifest.parent.mkdir(parents=True)
    bundled_manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    user_manifest = tmp_path / "user-active.json"
    states = {
        None: ({
            "active": True,
            "manifest_path": str(user_manifest),
            "database": {"path": str(user_db)},
            "manifest": {"semantic_index_path": ""},
        }, None, ()),
        str(bundled_manifest.resolve()): ({
            "active": True,
            "manifest_path": str(bundled_manifest),
            "database": {"path": str(bundled_db)},
            "manifest": {"semantic_index_path": ""},
        }, None, ()),
    }

    def fake_state(manifest_path: str | Path | None = None):
        return states[None if manifest_path is None else str(Path(manifest_path).resolve())]

    monkeypatch.setattr(knowledge_base_module, "_active_runtime_state", fake_state)
    result = pipeline_search_with_scope(
        ActiveRagScopeAdapter(), "混凝土裂缝修复", document_ids=["doc-bundled"], top_k=1
    )

    assert result.chunks
    assert {chunk.document_id for chunk in result.chunks} == {"doc-bundled"}
    assert "bundled_active_rag_selected_for_scope_compatibility" in result.warnings


def test_explicitly_disabled_active_rag_does_not_use_legacy_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = KnowledgeBase(tmp_path / "legacy")
    _add_legacy_document(kb, "doc-deleted", "删除前的旧知识库内容")
    from runtime import rag_production

    monkeypatch.setattr(
        rag_production,
        "active_rag_status",
        lambda: {"active": False, "reason": "catalog_empty", "legacy_fallback": False, "manifest": {"source_documents": []}},
    )
    with pytest.raises(RuntimeError, match="catalog_empty"):
        pipeline_search_with_scope(ActiveRagScopeAdapter(), "旧知识库内容", document_ids=[])
