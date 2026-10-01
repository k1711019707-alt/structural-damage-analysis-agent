from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import types
from pathlib import Path

import numpy as np
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_fixture(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    knowledge = project / "knowledge_base"
    candidate = knowledge / "production-rag-fixture"
    candidate.mkdir(parents=True)
    database = candidate / "knowledge_base_v2.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE pipeline_documents(
              document_id TEXT PRIMARY KEY,
              source_path TEXT,
              source_name TEXT,
              metadata_json TEXT
            );
            CREATE TABLE pipeline_chunks(
              chunk_id TEXT PRIMARY KEY,
              asset_path TEXT,
              metadata_json TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO pipeline_documents VALUES (?,?,?,?)",
            (
                "doc-a",
                r"C:\private\standards\source.pdf",
                "source.pdf",
                json.dumps({"source_path": r"C:\private\standards\source.pdf"}),
            ),
        )
        connection.execute(
            "INSERT INTO pipeline_chunks VALUES (?,?,?)",
            ("chunk-a", r"C:\private\assets\page.png", json.dumps({"token": ""})),
        )
    semantic = candidate / "semantic.npz"
    np.savez_compressed(
        semantic,
        vectors=np.ones((1, 2), dtype=np.float32),
        chunk_ids=np.asarray(["chunk-a"]),
        content_hashes=np.asarray(["hash-a"]),
    )
    semantic_manifest = Path(str(semantic) + ".manifest.json")
    semantic_manifest.write_text(
        json.dumps(
            {
                "schema_version": "knowledge-embeddings.v1",
                "model_name": "fixture/model",
                "model_path": r"C:\private\huggingface\fixture",
                "dimension": 2,
                "count": 1,
            }
        ),
        encoding="utf-8",
    )
    (candidate / "validation_report.json").write_text(
        json.dumps({"status": "ready", "source_path": r"C:\private\report.json"}), encoding="utf-8"
    )
    active = knowledge / "active_rag.json"
    source_dir = knowledge / "source_files"
    source_dir.mkdir()
    source_document = source_dir / "source.pdf"
    source_document.write_bytes(b"fixture-pdf")
    active.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "database_path": "production-rag-fixture/knowledge_base_v2.sqlite3",
                "database_sha256": _sha(database),
                "semantic_index_path": "production-rag-fixture/semantic.npz",
                "semantic_index_sha256": _sha(semantic),
                "semantic_manifest_path": "production-rag-fixture/semantic.npz.manifest.json",
                "semantic_manifest_sha256": _sha(semantic_manifest),
                "source_documents": [
                    {
                        "document_id": "doc-a",
                        "source_name": "source.pdf",
                        "source_path": r"C:\private\standards\source.pdf",
                        "source_sha256": _sha(source_document),
                    }
                ],
                "validation_evidence": {"report_path": r"C:\private\validation_report.json"},
                "rollback_manifest": r"C:\private\rollback.json",
                "previous": {"database_path": r"C:\private\old.sqlite3"},
            }
        ),
        encoding="utf-8",
    )
    model = tmp_path / "semantic-model"
    model.mkdir()
    (model / "config.json").write_text('{"hidden_size":2}', encoding="utf-8")
    fhl = tmp_path / "generate.mjs"
    fhl.write_text("export const fixture = true;", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.write_bytes(b"fixture-node")
    (project / "packaging").mkdir()
    (project / "packaging" / "fhl_runner.mjs").write_text("// fixture runner", encoding="utf-8")
    return {
        "project": project,
        "active": active,
        "database": database,
        "semantic": semantic,
        "model": model,
        "fhl": fhl,
        "node": node,
        "source_document": source_document,
    }


def _healthy(*_args, **_kwargs):
    return {"healthy": True, "reason": "ready"}


def test_portable_staging_sanitizes_and_binds_active_rag(tmp_path: Path) -> None:
    from tools.portable_release import prepare_portable_resources, sha256_file

    fixture = _portable_fixture(tmp_path)
    stage = tmp_path / "stage"
    result = prepare_portable_resources(
        project_root=fixture["project"],
        stage_root=stage,
        active_manifest=fixture["active"],
        fhl_script=fixture["fhl"],
        node_exe=fixture["node"],
        semantic_model_path=fixture["model"],
        version="2.1.0-test",
        health_validator=_healthy,
    )

    staged_active = json.loads((stage / "knowledge_base" / "active_rag.json").read_text(encoding="utf-8"))
    staged_db = stage / "knowledge_base" / staged_active["database_path"]
    staged_semantic_manifest = stage / "knowledge_base" / staged_active["semantic_manifest_path"]
    assert staged_active["database_sha256"] == sha256_file(staged_db)
    assert staged_active["previous"] == {}
    assert staged_active["rollback_manifest"] == ""
    assert staged_active["source_documents"][0]["source_path"] == "source_files/source.pdf"
    assert staged_active["source_documents"][0]["source_bundled"] is True
    assert json.loads(staged_semantic_manifest.read_text(encoding="utf-8"))["model_path"] == ""
    with sqlite3.connect(staged_db) as connection:
        assert connection.execute("SELECT source_path FROM pipeline_documents").fetchone()[0] == "source_files/source.pdf"
        metadata = json.loads(connection.execute("SELECT metadata_json FROM pipeline_documents").fetchone()[0])
        assert metadata["source_path"] == "source_files/source.pdf"
        assert connection.execute("SELECT asset_path FROM pipeline_chunks").fetchone()[0] == "page.png"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (stage / result["semantic_model"]["relative_path"] / "config.json").is_file()
    assert (stage / "fhl_plugin" / "generate.mjs").is_file()
    assert (stage / "fhl_plugin" / "node.exe").is_file()
    assert (stage / "knowledge_base" / "source_files" / "source.pdf").read_bytes() == b"fixture-pdf"
    assert result["product_version"] == "2.1.0-test"
    rendered = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in stage.rglob("*")
        if path.is_file() and path.suffix in {".json", ".mjs"}
    )
    assert r"C:\private" not in rendered
    assert result["active_rag"]["source_documents_bundled"] == 1


def test_portable_staging_rejects_nonempty_wal(tmp_path: Path) -> None:
    from tools.portable_release import PortableResourceError, prepare_portable_resources

    fixture = _portable_fixture(tmp_path)
    Path(str(fixture["database"]) + "-wal").write_bytes(b"active-write")
    with pytest.raises(PortableResourceError, match="non-empty WAL"):
        prepare_portable_resources(
            project_root=fixture["project"],
            stage_root=tmp_path / "stage",
            active_manifest=fixture["active"],
            fhl_script=fixture["fhl"],
            node_exe=fixture["node"],
            semantic_model_path=fixture["model"],
            version="2.1.0-test",
            health_validator=_healthy,
        )


def test_portable_staging_rejects_path_escape(tmp_path: Path) -> None:
    from tools.portable_release import PortableResourceError, prepare_portable_resources

    fixture = _portable_fixture(tmp_path)
    payload = json.loads(fixture["active"].read_text(encoding="utf-8"))
    payload["database_path"] = str((tmp_path / "outside.sqlite3").resolve())
    fixture["active"].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PortableResourceError, match="escapes the knowledge root"):
        prepare_portable_resources(
            project_root=fixture["project"],
            stage_root=tmp_path / "stage",
            active_manifest=fixture["active"],
            fhl_script=fixture["fhl"],
            node_exe=fixture["node"],
            semantic_model_path=fixture["model"],
            version="2.1.0-test",
            health_validator=_healthy,
        )


def test_portable_staging_rejects_changed_active_source(tmp_path: Path) -> None:
    from tools.portable_release import PortableResourceError, prepare_portable_resources

    fixture = _portable_fixture(tmp_path)
    fixture["source_document"].write_bytes(b"changed-after-activation")
    with pytest.raises(PortableResourceError, match="SHA-256 mismatch"):
        prepare_portable_resources(
            project_root=fixture["project"],
            stage_root=tmp_path / "stage",
            active_manifest=fixture["active"],
            fhl_script=fixture["fhl"],
            node_exe=fixture["node"],
            semantic_model_path=fixture["model"],
            version="2.1.0-test",
            health_validator=_healthy,
        )


def test_frozen_semantic_loader_uses_packaged_model_offline(monkeypatch, tmp_path: Path) -> None:
    import knowledge_pipeline.semantic_retrieve as semantic

    model_root = tmp_path / "embedding_models" / "fixture--model"
    model_root.mkdir(parents=True)
    (tmp_path / "embedding_models" / "model_manifest.json").write_text(
        json.dumps({"model_name": "fixture/model", "relative_path": "embedding_models/fixture--model"}),
        encoding="utf-8",
    )
    captured = {}

    class FakeSentenceTransformer:
        def __init__(self, path, **kwargs):
            captured.update({"path": path, **kwargs})

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    semantic._load_model("fixture/model")
    assert Path(captured["path"]) == model_root
    assert captured["local_files_only"] is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_source_release_semantic_model_is_loaded_offline(monkeypatch, tmp_path: Path) -> None:
    import knowledge_pipeline.semantic_retrieve as semantic
    import runtime.app_paths as app_paths

    model_root = tmp_path / "embedding_models" / "fixture-model"
    model_root.mkdir(parents=True)
    (tmp_path / "embedding_models" / "model_manifest.json").write_text(
        json.dumps({"model_name": "fixture/model", "relative_path": "embedding_models/fixture-model"}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeSentenceTransformer:
        def __init__(self, path, **kwargs):
            captured.update({"path": path, **kwargs})

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(app_paths, "application_resource_root", lambda: tmp_path)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    semantic._load_model("fixture/model")

    assert Path(captured["path"]) == model_root
    assert captured["local_files_only"] is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_source_release_fhl_defaults_to_bundled_script_and_node(monkeypatch, tmp_path: Path) -> None:
    import runtime.fhl_repair_renderer as renderer

    plugin = tmp_path / "fhl_plugin"
    plugin.mkdir()
    script = plugin / "generate.mjs"
    node = plugin / "node.exe"
    script.write_text("export const fixture = true;", encoding="utf-8")
    node.write_bytes(b"node")
    monkeypatch.delenv("FHL_IMAGE_GEN_SCRIPT", raising=False)
    monkeypatch.setattr(renderer, "application_resource_root", lambda: tmp_path)

    assert renderer.resolve_default_fhl_script() == script
    assert renderer.resolve_default_node_executable() == str(node)


def test_packaging_has_no_developer_absolute_paths_and_requires_staging() -> None:
    spec = (PROJECT_ROOT / "packaging" / "damage_workflow_desktop.spec").read_text(encoding="utf-8")
    build = (PROJECT_ROOT / "packaging" / "build_portable.ps1").read_text(encoding="utf-8")
    renderer = (PROJECT_ROOT / "runtime" / "fhl_repair_renderer.py").read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:\\", spec)
    assert not re.search(r"[A-Za-z]:\\", build)
    assert not re.search(r"C:\\Users\\17110", renderer)
    assert "YOLO11_PORTABLE_STAGE" in spec
    assert 'STAGE_ROOT / "knowledge_base"' in spec
    assert 'STAGE_ROOT / "embedding_models"' in spec
    assert '"runtime.assistant"' in spec
    assert '"runtime.siliconflow_repair_renderer"' in spec
    assert '"knowledge_pipeline"' in spec
    assert "ConfirmRagRebuildComplete" in build
    assert "tools\\portable_release.py" in build
    assert '"dist\\portable-$Version"' in build
    assert "Portable output bundle already exists" in build


def test_frozen_fhl_route_preserves_gui_bound_images_adapter() -> None:
    hook = (PROJECT_ROOT / "packaging" / "frozen_fhl_source_route.py").read_text(encoding="utf-8")
    runner = (PROJECT_ROOT / "packaging" / "fhl_runner.mjs").read_text(encoding="utf-8")

    assert "_render_with_python_api" not in hook
    assert "writeFileSync" not in runner
    assert "fhl-image-gen-config.json" not in runner


def test_startup_diagnostics_uses_packaged_product_version(monkeypatch, tmp_path: Path) -> None:
    import runtime.startup_diagnostics as diagnostics

    (tmp_path / "portable_stage_manifest.json").write_text(
        json.dumps({"product_version": "2.1.0-test"}), encoding="utf-8"
    )
    monkeypatch.setattr(diagnostics, "application_resource_root", lambda: tmp_path)
    assert diagnostics.application_version() == "2.1.0-test"


def test_release_inventory_and_redaction_checks(tmp_path: Path) -> None:
    from tools.verify_portable_release import (
        PortableVerificationError,
        _verify_inventory,
        _verify_no_secrets_or_absolute_sources,
    )

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"portable")
    release = {
        "files": [
            {
                "path": "payload.bin",
                "size_bytes": payload.stat().st_size,
                "sha256": _sha(payload),
            }
        ]
    }
    release_path = tmp_path / "release_manifest.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    assert _verify_inventory(tmp_path, release) == {"files": 1, "bytes": len(b"portable")}
    _verify_no_secrets_or_absolute_sources(tmp_path)

    release_path.write_text(json.dumps({**release, "source": r"C:\Users\private\build"}), encoding="utf-8")
    with pytest.raises(PortableVerificationError, match="source-machine user path"):
        _verify_no_secrets_or_absolute_sources(tmp_path)

    release_path.write_text(json.dumps({**release, "api_key": "literal-secret-value"}), encoding="utf-8")
    with pytest.raises(PortableVerificationError, match="credential-like"):
        _verify_no_secrets_or_absolute_sources(tmp_path)


def test_source_release_uses_portable_stage_and_excludes_user_state(tmp_path: Path) -> None:
    from tools.portable_release import prepare_portable_resources
    from tools.source_release import build_source_release, verify_source_release

    fixture = _portable_fixture(tmp_path)
    project = fixture["project"]
    (project / "runtime").mkdir()
    (project / "runtime" / "app.py").write_text("print('fixture')", encoding="utf-8")
    (project / "models").mkdir()
    (project / "models" / "best.pt").write_bytes(b"model")
    (project / "environment.yml").write_text("name: fixture\nprefix: C:\\private\\env\n", encoding="utf-8")
    (project / "requirements-lock.txt").write_text("torch==fixture\n", encoding="utf-8")
    (project / "start_yolo11s_seg_gui.bat").write_text("python scripts\\launch.py", encoding="utf-8")
    (project / "gui_api_config.json").write_text('{"api_key":"secret-value"}', encoding="utf-8")
    stage = tmp_path / "stage"
    prepare_portable_resources(
        project_root=project,
        stage_root=stage,
        active_manifest=fixture["active"],
        fhl_script=fixture["fhl"],
        node_exe=fixture["node"],
        semantic_model_path=fixture["model"],
        version="2.0.0",
        health_validator=_healthy,
    )
    candidate = tmp_path / "source-release"

    result = build_source_release(project, stage, candidate, version="2.0.0")
    verified = verify_source_release(candidate)

    assert result["version"] == "2.0.0"
    assert verified["status"] == "ready"
    assert (candidate / "runtime" / "app.py").is_file()
    assert (candidate / "knowledge_base" / "active_rag.json").is_file()
    assert (candidate / "embedding_models" / "model_manifest.json").is_file()
    assert (candidate / "fhl_plugin" / "node.exe").is_file()
    assert (candidate / "portable_stage_manifest.json").is_file()
    assert "prefix:" not in (candidate / "environment.yml").read_text(encoding="utf-8")
    assert not (candidate / "gui_api_config.json").exists()
    assert verified["knowledge_base"]["source_documents_bundled"] == 1
