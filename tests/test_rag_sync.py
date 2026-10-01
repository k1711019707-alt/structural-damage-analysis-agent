from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import pytest

from runtime.rag_sync import resolve_profile_folder_scope, synchronize_settings_scopes, user_facing_sync_error


@dataclass
class Folder:
    folder_id: str


class FakeKnowledgeBase:
    def __init__(self) -> None:
        self.folders = [Folder("root"), Folder("other")]

    def list_folders(self):
        return self.folders

    def document_ids_for_folders(self, ids):
        return {"root": ["doc-a", "doc-stale"], "other": ["doc-b"]}.get(next(iter(ids), ""), [])


@dataclass
class Profile:
    knowledge_base_folder_ids: list[str]
    knowledge_base_document_ids: list[str]


@dataclass
class Settings:
    knowledge_base: object
    generation_profiles: dict[str, Profile]


def test_folder_scope_replaces_hidden_document_ids_and_filters_inactive() -> None:
    profile = Profile(["root"], ["old-hidden"])
    result = resolve_profile_folder_scope(profile, FakeKnowledgeBase(), {"doc-a"})
    assert profile.knowledge_base_document_ids == ["doc-a"]
    assert result["excluded_not_active"] == ["doc-stale"]


def test_empty_folder_selection_clears_explicit_ids() -> None:
    profile = Profile([], ["old-hidden"])
    result = resolve_profile_folder_scope(profile, FakeKnowledgeBase(), {"doc-a"})
    assert profile.knowledge_base_document_ids == []
    assert result["document_ids"] == []


def test_global_and_profile_scopes_are_normalized_to_active_ids() -> None:
    settings = Settings(
        knowledge_base=type("KB", (), {"enabled_document_ids": ["old"]})(),
        generation_profiles={
            "report": Profile(["root"], ["old-hidden"]),
            "plan": Profile(["missing"], ["old-hidden"]),
        },
    )
    result = synchronize_settings_scopes(settings, FakeKnowledgeBase(), {"doc-a", "doc-b"})
    assert settings.knowledge_base.enabled_document_ids == ["doc-a", "doc-b"]
    assert settings.generation_profiles["report"].knowledge_base_document_ids == ["doc-a"]
    assert settings.generation_profiles["plan"].knowledge_base_document_ids == []
    assert result["active_document_ids"] == ["doc-a", "doc-b"]


def test_rebuild_uses_versioned_candidate_and_strict_activation(monkeypatch, tmp_path: Path) -> None:
    import runtime.rag_sync as sync
    import runtime.knowledge_base as kb_module
    import scripts.build_production_rag as builder

    root = tmp_path / "user-kb"
    source = root / "source_files" / "reference.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf")
    catalog = kb_module.KnowledgeBase(root)
    catalog.register_ready_document(document_id="doc-a", path=source, sha256="sha")
    monkeypatch.setattr(sync, "project_knowledge_base_root", lambda: tmp_path / "project-kb")
    monkeypatch.setattr(sync, "active_document_ids", lambda: {"doc-a"})
    captured = {}

    def fake_build(source_dir, output_dir, **kwargs):
        captured.update({"source_dir": source_dir, "output_dir": output_dir, **kwargs})
        return {"status": "ready", "activation_result": {"activated": True}}

    monkeypatch.setattr(builder, "build", fake_build)
    result = sync.rebuild_and_activate(root)
    assert captured["activate"] is True
    assert captured["require_v2_scope"] is True
    assert captured["remote_blank_review"] is True
    assert captured["include_names"] == {"reference.pdf"}
    assert captured["legacy_db"].name == "catalog_snapshot.sqlite3"
    assert captured["legacy_db"] != root / "knowledge_base.sqlite3"
    assert captured["output_dir"].parent == tmp_path / "project-kb"
    assert captured["output_dir"].name.startswith("production-rag-gui-")
    assert result["active_document_ids"] == ["doc-a"]


def test_frozen_rebuild_uses_user_production_root(monkeypatch, tmp_path: Path) -> None:
    import runtime.rag_sync as sync

    user_root = tmp_path / "user-kb"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sync, "production_rag_write_root", lambda: user_root)
    monkeypatch.setattr(sync, "project_knowledge_base_root", lambda: tmp_path / "bundle-kb")

    assert sync._production_build_root() == user_root


def test_source_rebuild_keeps_project_production_root(monkeypatch, tmp_path: Path) -> None:
    import runtime.rag_sync as sync

    project_root = tmp_path / "project-kb"
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sync, "production_rag_write_root", lambda: tmp_path / "user-kb")
    monkeypatch.setattr(sync, "project_knowledge_base_root", lambda: project_root)

    assert sync._production_build_root() == project_root


def test_rebuild_rejects_unactivated_candidate(monkeypatch, tmp_path: Path) -> None:
    import runtime.rag_sync as sync
    import runtime.knowledge_base as kb_module
    import scripts.build_production_rag as builder

    root = tmp_path / "user-kb"
    source = root / "source_files" / "reference.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf")
    catalog = kb_module.KnowledgeBase(root)
    catalog.register_ready_document(document_id="doc-a", path=source, sha256="sha")
    monkeypatch.setattr(sync, "project_knowledge_base_root", lambda: tmp_path / "project-kb")
    monkeypatch.setattr(
        builder,
        "build",
        lambda *args, **kwargs: {
            "status": "failed",
            "activation_result": {"activated": False, "reason": "candidate_validation_failed"},
        },
    )
    with pytest.raises(RuntimeError, match="candidate_validation_failed"):
        sync.rebuild_and_activate(root)


def test_rebuild_empty_catalog_explicitly_disables_active_rag(monkeypatch, tmp_path: Path) -> None:
    import runtime.rag_sync as sync
    import runtime.knowledge_base as kb_module
    import runtime.rag_production as production

    root = tmp_path / "user-kb"
    kb_module.KnowledgeBase(root)
    active_manifest = tmp_path / "project-kb" / "active_rag.json"
    monkeypatch.setattr(sync, "project_knowledge_base_root", lambda: active_manifest.parent)
    monkeypatch.setattr(production, "active_rag_manifest_path", lambda: active_manifest)
    monkeypatch.setattr(production, "active_rag_storage_scope", lambda: "project")

    result = sync.rebuild_and_activate(root)
    assert result == {"status": "disabled", "disabled": True, "reason": "catalog_empty", "active_document_ids": []}
    status = production.active_rag_status()
    assert status["active"] is False
    assert status["reason"] == "catalog_empty"
    assert status["legacy_fallback"] is False
    assert status["manifest"]["source_documents"] == []


def test_catalog_snapshot_is_immutable_after_live_deletion(tmp_path: Path) -> None:
    import sqlite3
    import runtime.rag_sync as sync
    import runtime.knowledge_base as kb_module

    root = tmp_path / "user-kb"
    source = root / "source_files" / "reference.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf")
    catalog = kb_module.KnowledgeBase(root)
    catalog.register_ready_document(document_id="doc-a", path=source, sha256="sha")
    snapshot = sync._snapshot_catalog(root / "knowledge_base.sqlite3", tmp_path / "snapshot.sqlite3")
    catalog.remove_document("doc-a")

    assert sync._snapshot_ready_records(snapshot)[0].document_id == "doc-a"
    with sqlite3.connect(root / "knowledge_base.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_sync_validation_error_is_readable_and_keeps_report_path() -> None:
    error = RuntimeError("生产 RAG 候选未激活：{'reason': 'candidate_validation_failed', 'validation_report': 'C:/kb/validation_report.json'}")
    message = user_facing_sync_error(error)
    assert "候选校验未通过" in message
    assert "C:/kb/validation_report.json" in message
    assert "{'reason'" not in message


def test_bootstrap_active_catalog_registers_sources_without_reextracting(monkeypatch, tmp_path: Path) -> None:
    import runtime.rag_sync as sync

    source = tmp_path / "active.pdf"
    source.write_bytes(b"active")
    settings = Settings(
        knowledge_base=type("KB", (), {"enabled_document_ids": []})(),
        generation_profiles={"report": Profile([], ["doc-a"])},
    )

    class Catalog(FakeKnowledgeBase):
        def __init__(self):
            self.folders = []
            self.registered = []

        def list_documents(self):
            return []

        def create_folder(self, name):
            folder = Folder(name)
            self.folders.append(folder)
            return type("FolderRecord", (), {"folder_id": name, "name": name, "parent_id": ""})()

        def register_ready_document(self, **kwargs):
            self.registered.append(kwargs)

    catalog = Catalog()
    monkeypatch.setattr(sync, "active_rag_snapshot", lambda: {
        "document_ids": ["doc-a"],
        "source_documents": [{"document_id": "doc-a", "source_path": str(source), "source_sha256": "sha"}],
    })
    result = sync.bootstrap_active_catalog(settings, catalog)
    assert result["imported_document_ids"] == ["doc-a"]
    assert catalog.registered[0]["document_id"] == "doc-a"
    assert settings.knowledge_base.enabled_document_ids == ["doc-a"]


def test_bootstrap_resolves_bundled_source_relative_to_selected_manifest(monkeypatch, tmp_path: Path) -> None:
    import runtime.rag_sync as sync

    bundled_root = tmp_path / "bundle" / "knowledge_base"
    source = bundled_root / "source_files" / "active.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"active")
    manifest = bundled_root / "active_rag.json"
    manifest.write_text("{}", encoding="utf-8")
    settings = Settings(
        knowledge_base=type("KB", (), {"enabled_document_ids": []})(),
        generation_profiles={"report": Profile([], ["doc-a"])},
    )

    class Catalog(FakeKnowledgeBase):
        def __init__(self):
            self.folders = []
            self.registered = []

        def list_documents(self):
            return []

        def create_folder(self, name):
            folder = Folder(name)
            self.folders.append(folder)
            return type("FolderRecord", (), {"folder_id": name, "name": name, "parent_id": ""})()

        def register_ready_document(self, **kwargs):
            self.registered.append(kwargs)

    catalog = Catalog()
    monkeypatch.setattr(sync, "active_rag_snapshot", lambda: {
        "document_ids": ["doc-a"],
        "manifest_path": str(manifest),
        "source_documents": [{
            "document_id": "doc-a",
            "source_path": "source_files/active.pdf",
            "source_sha256": "sha",
        }],
    })

    result = sync.bootstrap_active_catalog(settings, catalog)

    assert result["imported_document_ids"] == ["doc-a"]
    assert catalog.registered[0]["path"] == source


def test_bootstrap_repairs_profile_folder_ids_from_existing_catalog(monkeypatch, tmp_path: Path) -> None:
    import runtime.rag_sync as sync

    settings = Settings(
        knowledge_base=type("KB", (), {"enabled_document_ids": []})(),
        generation_profiles={"report": Profile(["stale-folder"], ["doc-a"])},
    )

    class Catalog:
        def list_documents(self):
            return [type("Record", (), {"document_id": "doc-a", "folder_id": "current-folder"})()]

        def list_folders(self):
            return [type("FolderRecord", (), {"folder_id": "current-folder", "name": "current", "parent_id": ""})()]

    monkeypatch.setattr(sync, "active_rag_snapshot", lambda: {
        "document_ids": ["doc-a"], "source_documents": [{"document_id": "doc-a"}],
    })
    result = sync.bootstrap_active_catalog(settings, Catalog())
    assert result["imported_document_ids"] == []
    assert settings.generation_profiles["report"].knowledge_base_folder_ids == ["current-folder"]
