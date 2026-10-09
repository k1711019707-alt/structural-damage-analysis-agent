"""Build and verify a versioned source release from an immutable portable stage."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOURCE_DIRECTORIES = (
    "configs",
    "deployment",
    "docs",
    "knowledge_pipeline",
    "models",
    "packaging",
    "runtime",
    "scripts",
    "templates",
    "tests",
    "tools",
)
SOURCE_FILES = (
    "environment.yml",
    "pytest.ini",
    "requirements-lock.txt",
    "start_yolo11s_seg_gui.bat",
)
EXCLUDED_DIRECTORY_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "test",
    "test_results",
    "results",
    "fhl_plugin",
}
EXCLUDED_FILE_NAMES = {
    "gui_api_config.json",
    "gui_api_config.json.legacy-backup",
    "gui_settings.json",
    ".env",
}


class SourceReleaseError(RuntimeError):
    """Raised when a source candidate violates the release contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _excluded(relative: Path) -> bool:
    return (
        any(part in EXCLUDED_DIRECTORY_NAMES for part in relative.parts[:-1])
        or relative.name in EXCLUDED_FILE_NAMES
        or relative.suffix.casefold() in {".pyc", ".pyo"}
    )


def _copy_source_tree(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if _excluded(relative):
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold())
        if path.is_file() and path.name != "source_release_manifest.json"
    ]


def _sanitize_environment(path: Path) -> None:
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join(line for line in lines if not line.lstrip().casefold().startswith("prefix:")) + "\n",
        encoding="utf-8",
    )


def build_source_release(
    project_root: str | Path,
    portable_stage: str | Path,
    destination: str | Path,
    *,
    version: str,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    stage = Path(portable_stage).resolve()
    target = Path(destination).resolve()
    if target.exists():
        raise SourceReleaseError(f"source release destination already exists: {target}")
    stage_manifest_path = stage / "portable_stage_manifest.json"
    if not stage_manifest_path.is_file():
        raise SourceReleaseError("portable staging manifest is missing")
    stage_manifest = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
    if str(stage_manifest.get("product_version") or "") != str(version):
        raise SourceReleaseError("source release version does not match portable stage")
    try:
        target.mkdir(parents=True)
        for directory in SOURCE_DIRECTORIES:
            source = project / directory
            if source.is_dir():
                _copy_source_tree(source, target / directory)
        for filename in SOURCE_FILES:
            source = project / filename
            if source.is_file():
                shutil.copy2(source, target / filename)
        for resource in ("knowledge_base", "embedding_models", "fhl_plugin"):
            source = stage / resource
            if not source.is_dir():
                raise SourceReleaseError(f"portable stage is missing {resource}")
            shutil.copytree(source, target / resource)
        shutil.copy2(stage_manifest_path, target / "portable_stage_manifest.json")
        _sanitize_environment(target / "environment.yml")
        (target / "SOURCE_RELEASE_README.md").write_text(
            "# YOLO11DamageDesktop source release\n\n"
            "This source candidate contains the formal model, immutable production RAG, active source documents, "
            "offline semantic model, and FHL/Node runtime. It intentionally excludes API credentials and user settings.\n\n"
            "## Start on another Windows computer\n\n"
            "1. Create the environment with `conda env create -f environment.yml`.\n"
            "2. Activate it with `conda activate YOLO11-HAI`.\n"
            "3. Run `start_yolo11s_seg_gui.bat`.\n"
            "4. Configure remote API credentials in the GUI on that computer; credentials are not distributed.\n",
            encoding="utf-8",
        )
        active = json.loads((target / "knowledge_base" / "active_rag.json").read_text(encoding="utf-8"))
        manifest = {
            "schema_version": "source-release.v1",
            "product": "YOLO11DamageDesktop",
            "version": str(version),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "entrypoint": "scripts/launch_yolo11s_seg_gui.py",
            "formal_model": {
                "path": "models/best.pt",
                "sha256": sha256_file(target / "models" / "best.pt"),
            },
            "knowledge_base": {
                "manifest": "knowledge_base/active_rag.json",
                "database_path": str(active.get("database_path") or ""),
                "database_sha256": str(active.get("database_sha256") or ""),
                "semantic_index_path": str(active.get("semantic_index_path") or ""),
                "semantic_index_sha256": str(active.get("semantic_index_sha256") or ""),
                "source_documents_bundled": len(active.get("source_documents") or []),
            },
            "semantic_model": stage_manifest.get("semantic_model") or {},
            "fhl": stage_manifest.get("fhl") or {},
            "files": _inventory(target),
        }
        (target / "source_release_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        verify_source_release(target)
        return manifest
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def verify_source_release(candidate: str | Path) -> dict[str, Any]:
    root = Path(candidate).resolve()
    manifest_path = root / "source_release_manifest.json"
    if not manifest_path.is_file():
        raise SourceReleaseError("source release manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise SourceReleaseError("source release inventory is empty")
    listed: set[str] = set()
    for row in rows:
        relative = str(row.get("path") or "").replace("\\", "/")
        if not relative or relative in listed or relative.startswith("../") or "/../" in relative:
            raise SourceReleaseError(f"invalid source inventory path: {relative}")
        listed.add(relative)
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SourceReleaseError(f"source inventory path escapes candidate: {relative}") from exc
        if not path.is_file() or path.stat().st_size != int(row.get("size_bytes", -1)):
            raise SourceReleaseError(f"source release file missing or changed: {relative}")
        if sha256_file(path) != str(row.get("sha256") or "").casefold():
            raise SourceReleaseError(f"source release digest mismatch: {relative}")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "source_release_manifest.json"
    }
    if actual != listed:
        raise SourceReleaseError("source release inventory is incomplete")
    for forbidden in EXCLUDED_FILE_NAMES:
        if any(path.name == forbidden for path in root.rglob("*")):
            raise SourceReleaseError(f"source release contains forbidden user state: {forbidden}")
    environment = (root / "environment.yml").read_text(encoding="utf-8")
    if any(line.lstrip().casefold().startswith("prefix:") for line in environment.splitlines()):
        raise SourceReleaseError("source environment contains a developer-machine prefix")
    required = (
        root / "models" / "best.pt",
        root / "knowledge_base" / "active_rag.json",
        root / "embedding_models" / "model_manifest.json",
        root / "fhl_plugin" / "generate.mjs",
        root / "fhl_plugin" / "node.exe",
        root / "portable_stage_manifest.json",
        root / "start_yolo11s_seg_gui.bat",
    )
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise SourceReleaseError(f"source release is missing required files: {missing}")
    knowledge = manifest.get("knowledge_base") if isinstance(manifest.get("knowledge_base"), dict) else {}
    return {
        "status": "ready",
        "candidate": str(root),
        "version": str(manifest.get("version") or ""),
        "files": len(listed),
        "knowledge_base": knowledge,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify a YOLO11DamageDesktop source release")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--portable-stage", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--version")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    result = (
        verify_source_release(args.destination)
        if args.verify_only
        else build_source_release(
            args.project_root or Path.cwd(),
            args.portable_stage or Path(),
            args.destination,
            version=str(args.version or ""),
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
