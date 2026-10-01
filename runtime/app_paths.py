"""Portable resource and user-data paths for source and frozen runtimes."""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "YOLO11DamageDesktop"


def application_resource_root() -> Path:
    """Return the read-only resource root for source or PyInstaller execution."""
    if bool(getattr(sys, "frozen", False)):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[1]


def user_data_root() -> Path:
    """Return the current user's writable application-data directory."""
    override = os.environ.get("YOLO11_DAMAGE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / f".{APP_NAME}"


def user_config_path(filename: str) -> Path:
    return user_data_root() / "config" / filename


def user_knowledge_base_root() -> Path:
    return user_data_root() / "knowledge_base"


def project_knowledge_base_root() -> Path:
    """Return the project-local knowledge-base root used by portable releases."""
    return application_resource_root() / "knowledge_base"


def project_active_rag_manifest_path() -> Path:
    """Return the optional project-local active RAG manifest."""
    return project_knowledge_base_root() / "active_rag.json"


def user_active_rag_manifest_path() -> Path:
    return user_knowledge_base_root() / "active_rag.json"


def active_rag_manifest_path() -> Path:
    """Return the selected manifest, layering frozen user state over the bundle."""
    # An explicit data-root override is used by tests, isolated deployments and
    # recovery tooling; it must be authoritative over a bundled project copy.
    if os.environ.get("YOLO11_DAMAGE_DATA_DIR", "").strip():
        return user_active_rag_manifest_path()
    user_manifest = user_active_rag_manifest_path()
    if bool(getattr(sys, "frozen", False)) and user_manifest.is_file():
        return user_manifest
    project_manifest = project_active_rag_manifest_path()
    if project_manifest.is_file():
        return project_manifest
    return user_manifest


def active_rag_write_manifest_path() -> Path:
    """Return the writable activation target without mutating frozen resources."""
    if os.environ.get("YOLO11_DAMAGE_DATA_DIR", "").strip() or bool(getattr(sys, "frozen", False)):
        return user_active_rag_manifest_path()
    return project_active_rag_manifest_path()


def production_rag_write_root() -> Path:
    return active_rag_write_manifest_path().parent


def active_rag_storage_scope() -> str:
    """Identify whether the selected active manifest is project-local or user data."""
    return "project" if active_rag_manifest_path() == project_active_rag_manifest_path() else "user_data"


def _manifest_relative_path(path_value: str | Path | None, manifest: Path) -> Path | None:
    return resolve_knowledge_portable_path(path_value, root=manifest.parent)


def active_rag_db_path() -> Path:
    """Return the configured v2 database path, or the default versioned path."""
    manifest = active_rag_manifest_path()
    if manifest.is_file():
        try:
            import json

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            value = str(payload.get("database_path") or "").strip()
            if value:
                resolved = _manifest_relative_path(value, manifest)
                if resolved is not None:
                    return resolved
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return user_knowledge_base_root() / "knowledge_base_v2.sqlite3"


def active_rag_semantic_index_path() -> Path | None:
    manifest = active_rag_manifest_path()
    if not manifest.is_file():
        return None
    try:
        import json

        payload = json.loads(manifest.read_text(encoding="utf-8"))
        value = str(payload.get("semantic_index_path") or "").strip()
        if not value:
            return None
        return _manifest_relative_path(value, manifest)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def resolve_knowledge_portable_path(path_value: str | Path | None, *, root: str | Path | None = None) -> Path | None:
    """Resolve manifest paths without allowing relative paths to escape the selected KB root."""
    text = str(path_value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    # Drive-relative paths such as C:foo and UNC/device-relative spellings are
    # ambiguous and are never accepted as portable relative paths.
    if (path.drive and not path.is_absolute()) or text.startswith(("\\\\", "//")):
        return None
    if path.is_absolute():
        return path.resolve()
    root_path = Path(root).expanduser().resolve() if root is not None else user_knowledge_base_root().resolve()
    candidate = (root_path / path).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError:
        return None
    return candidate


def default_output_root() -> Path:
    documents = os.environ.get("USERPROFILE", "").strip()
    if documents:
        return Path(documents) / "Documents" / APP_NAME / "Outputs"
    return user_data_root() / "Outputs"


def resolve_user_path(path_value: str | Path | None, *, default: Path | None = None) -> Path:
    """Resolve configured relative user paths below the writable data root."""
    if path_value in (None, ""):
        if default is None:
            raise ValueError("path_value or default is required")
        return default
    path = Path(path_value).expanduser()
    if path.is_absolute():
        # Older development settings stored the project-local knowledge-base
        # directory as an absolute path.  Do not keep following that path on a
        # different computer; migrate it to the writable per-user location.
        legacy_root = Path(__file__).resolve().parents[1] / "knowledge_base"
        try:
            if path.resolve() == legacy_root.resolve() and default is not None:
                return Path(default)
        except OSError:
            pass
        return path
    return user_data_root() / path
