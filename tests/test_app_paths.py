from __future__ import annotations

import sys
from pathlib import Path


def test_source_resource_root_points_to_project() -> None:
    from runtime.app_paths import application_resource_root

    assert application_resource_root() == Path(__file__).resolve().parents[1]


def test_frozen_resource_root_uses_meipass(monkeypatch, tmp_path: Path) -> None:
    import runtime.app_paths as module

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert module.application_resource_root() == tmp_path


def test_user_data_override_and_relative_paths(monkeypatch, tmp_path: Path) -> None:
    import runtime.app_paths as module

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "数据 目录"))
    assert module.user_data_root() == tmp_path / "数据 目录"
    assert module.user_config_path("gui_settings.json") == tmp_path / "数据 目录" / "config" / "gui_settings.json"
    assert module.resolve_user_path("知识库") == tmp_path / "数据 目录" / "知识库"


def test_legacy_project_knowledge_base_migrates_to_default(monkeypatch, tmp_path: Path) -> None:
    import runtime.app_paths as module

    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "user"))
    legacy = Path(__file__).resolve().parents[1] / "knowledge_base"
    default = module.user_knowledge_base_root()
    assert module.resolve_user_path(legacy, default=default) == default


def test_default_output_root_respects_userprofile(monkeypatch, tmp_path: Path) -> None:
    import runtime.app_paths as module

    monkeypatch.setenv("USERPROFILE", str(tmp_path / "用户"))
    assert module.default_output_root() == tmp_path / "用户" / "Documents" / module.APP_NAME / "Outputs"


def test_project_local_active_manifest_takes_precedence(monkeypatch, tmp_path: Path) -> None:
    import json
    import runtime.app_paths as module

    project_root = tmp_path / "project"
    user_root = tmp_path / "user"
    monkeypatch.setattr(module, "application_resource_root", lambda: project_root)
    monkeypatch.delenv("YOLO11_DAMAGE_DATA_DIR", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    project_kb = project_root / "knowledge_base"
    user_kb = user_root / "knowledge_base"
    project_kb.mkdir(parents=True)
    user_kb.mkdir(parents=True)
    (project_kb / "active_rag.json").write_text(json.dumps({"database_path": "v11/db.sqlite3"}), encoding="utf-8")
    (user_kb / "active_rag.json").write_text(json.dumps({"database_path": "legacy/db.sqlite3"}), encoding="utf-8")

    assert module.active_rag_manifest_path() == project_kb / "active_rag.json"
    assert module.active_rag_storage_scope() == "project"
    assert module.active_rag_db_path() == (project_kb / "v11/db.sqlite3").resolve()


def test_frozen_active_manifest_uses_bundled_baseline_until_user_override_exists(monkeypatch, tmp_path: Path) -> None:
    import json
    import runtime.app_paths as module

    project_root = tmp_path / "bundle"
    user_root = tmp_path / "user"
    monkeypatch.setattr(module, "application_resource_root", lambda: project_root)
    monkeypatch.setattr(module, "user_data_root", lambda: user_root)
    monkeypatch.delenv("YOLO11_DAMAGE_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    bundled_manifest = project_root / "knowledge_base" / "active_rag.json"
    bundled_manifest.parent.mkdir(parents=True)
    bundled_manifest.write_text(json.dumps({"database_path": "bundled/db.sqlite3"}), encoding="utf-8")

    assert module.active_rag_manifest_path() == bundled_manifest
    assert module.active_rag_storage_scope() == "project"
    assert module.active_rag_write_manifest_path() == user_root / "knowledge_base" / "active_rag.json"
    assert module.production_rag_write_root() == user_root / "knowledge_base"

    user_manifest = user_root / "knowledge_base" / "active_rag.json"
    user_manifest.parent.mkdir(parents=True)
    user_manifest.write_text(json.dumps({"database_path": "user/db.sqlite3"}), encoding="utf-8")

    assert module.active_rag_manifest_path() == user_manifest
    assert module.active_rag_storage_scope() == "user_data"
    assert module.active_rag_db_path() == (user_root / "knowledge_base" / "user/db.sqlite3").resolve()


def test_active_manifest_falls_back_to_user_data(monkeypatch, tmp_path: Path) -> None:
    import json
    import runtime.app_paths as module

    project_root = tmp_path / "project"
    user_root = tmp_path / "user"
    monkeypatch.setattr(module, "application_resource_root", lambda: project_root)
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(user_root))
    user_kb = user_root / "knowledge_base"
    user_kb.mkdir(parents=True)
    (user_kb / "active_rag.json").write_text(json.dumps({"database_path": "legacy/db.sqlite3"}), encoding="utf-8")

    assert module.active_rag_manifest_path() == user_kb / "active_rag.json"
    assert module.active_rag_storage_scope() == "user_data"
    assert module.active_rag_db_path() == (user_kb / "legacy/db.sqlite3").resolve()
