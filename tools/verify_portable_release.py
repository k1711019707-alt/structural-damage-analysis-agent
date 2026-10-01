"""Verify a complete extracted YOLO11DamageDesktop onedir release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(?:api[_-]?key|apikey|token|secret|password)\s*['\"]?\s*[:=]\s*(['\"])[^'\"\r\n]{8,}\1"),
)
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


class PortableVerificationError(RuntimeError):
    """Raised when a portable candidate does not meet its release contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(root: Path, value: str, *, label: str) -> Path:
    candidate = (root / str(value or "")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PortableVerificationError(f"{label} escapes the portable bundle: {value}") from exc
    return candidate


def _verify_inventory(bundle: Path, release: dict[str, Any]) -> dict[str, int]:
    rows = release.get("files")
    if not isinstance(rows, list) or not rows:
        raise PortableVerificationError("release_manifest.json has no file inventory")
    listed: set[str] = set()
    total_bytes = 0
    for row in rows:
        if not isinstance(row, dict):
            raise PortableVerificationError("release file inventory contains a non-object row")
        relative = str(row.get("path") or "").replace("\\", "/")
        if not relative or relative in listed:
            raise PortableVerificationError(f"release file inventory has a missing or duplicate path: {relative}")
        listed.add(relative)
        path = _inside(bundle, relative, label="release file")
        if not path.is_file():
            raise PortableVerificationError(f"release file is missing: {relative}")
        expected_size = int(row.get("size_bytes", -1))
        if path.stat().st_size != expected_size:
            raise PortableVerificationError(f"release file size mismatch: {relative}")
        expected_sha = str(row.get("sha256") or "").casefold()
        if sha256_file(path).casefold() != expected_sha:
            raise PortableVerificationError(f"release file SHA-256 mismatch: {relative}")
        total_bytes += expected_size
    actual = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path.name != "release_manifest.json"
    }
    extras = sorted(actual - listed)
    missing = sorted(listed - actual)
    if extras or missing:
        raise PortableVerificationError(
            f"release inventory is not complete: unlisted={extras[:5]} missing={missing[:5]}"
        )
    return {"files": len(listed), "bytes": total_bytes}


def _absolute_source_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _absolute_source_values(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _absolute_source_values(nested)]
    if isinstance(value, str) and (WINDOWS_ABSOLUTE.match(value) or value.startswith(("\\\\", "//"))):
        return [value]
    return []


def _verify_no_secrets_or_absolute_sources(bundle: Path) -> None:
    manifest_text = (bundle / "release_manifest.json").read_text(encoding="utf-8", errors="ignore")
    if any(pattern.search(manifest_text) for pattern in SECRET_PATTERNS):
        raise PortableVerificationError("release manifest contains a credential-like value")
    manifest = json.loads(manifest_text)
    if _absolute_source_values(manifest):
        raise PortableVerificationError("release manifest contains a source-machine user path")


def _verify_rag(bundle: Path, release: dict[str, Any]) -> dict[str, Any]:
    knowledge = release.get("knowledge_base")
    if not isinstance(knowledge, dict) or knowledge.get("mode") != "bundled-active-rag":
        raise PortableVerificationError("release manifest does not declare bundled active RAG")
    internal = bundle / "_internal"
    active_path = _inside(internal, str(knowledge.get("manifest") or ""), label="active RAG manifest")
    if not active_path.is_file():
        raise PortableVerificationError("bundled active RAG manifest is missing")
    active = json.loads(active_path.read_text(encoding="utf-8"))
    database = _inside(active_path.parent, str(active.get("database_path") or ""), label="RAG database")
    semantic = _inside(active_path.parent, str(active.get("semantic_index_path") or ""), label="semantic index")
    from runtime.rag_production import inspect_v2_database

    health = inspect_v2_database(
        database,
        expected_sha256=str(active.get("database_sha256") or ""),
        semantic_index_path=semantic,
        expected_semantic_index_sha256=str(active.get("semantic_index_sha256") or ""),
        expected_semantic_manifest_sha256=str(active.get("semantic_manifest_sha256") or ""),
        semantic_root=active_path.parent,
        strict=True,
    )
    if not health.get("healthy"):
        raise PortableVerificationError(f"bundled active RAG is unhealthy: {health.get('reason', 'unknown')}")
    return {
        "healthy": True,
        "documents": int(health.get("documents") or 0),
        "retrieval_children": int(health.get("retrieval_children") or 0),
        "semantic_count": int((health.get("semantic") or {}).get("retrieval_count") or 0),
        "semantic_index": semantic,
    }


def _verify_semantic_model(bundle: Path, release: dict[str, Any], semantic_index: Path) -> dict[str, Any]:
    metadata = release.get("semantic_model")
    if not isinstance(metadata, dict):
        raise PortableVerificationError("release manifest does not declare a semantic model")
    model_name = str(metadata.get("model_name") or "")
    model_path = _inside(bundle / "_internal", str(metadata.get("relative_path") or ""), label="semantic model")
    if not model_path.is_dir():
        raise PortableVerificationError("bundled semantic model directory is missing")
    manifest_path = Path(str(semantic_index) + ".manifest.json")
    semantic_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_dimension = int(semantic_manifest.get("dimension") or 0)
    previous_hf = os.environ.get("HF_HUB_OFFLINE")
    previous_transformers = os.environ.get("TRANSFORMERS_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(str(model_path), local_files_only=True, device="cpu")
        vector = model.encode(
            ["混凝土结构裂缝检测与修复"],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except Exception as exc:
        raise PortableVerificationError(f"bundled semantic model cannot encode offline: {type(exc).__name__}") from exc
    finally:
        if previous_hf is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous_hf
        if previous_transformers is None:
            os.environ.pop("TRANSFORMERS_OFFLINE", None)
        else:
            os.environ["TRANSFORMERS_OFFLINE"] = previous_transformers
    dimension = int(vector.shape[1])
    if expected_dimension <= 0 or dimension != expected_dimension:
        raise PortableVerificationError(
            f"bundled semantic model dimension mismatch: expected={expected_dimension} actual={dimension}"
        )
    return {"model_name": model_name, "dimension": dimension, "offline": True}


def _verify_node(bundle: Path) -> dict[str, str]:
    plugin = bundle / "_internal" / "fhl_plugin"
    node = plugin / "node.exe"
    runner = plugin / "fhl_runner.mjs"
    generate = plugin / "generate.mjs"
    for script in (runner, generate):
        completed = subprocess.run(
            [str(node), "--check", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise PortableVerificationError(f"bundled Node syntax check failed: {script.name}")
    return {"runner": "ok", "generate": "ok"}


def _verify_executable(bundle: Path, seconds: float) -> dict[str, Any]:
    executable = bundle / "YOLO11DamageDesktop.exe"
    if not executable.is_file():
        raise PortableVerificationError("portable executable is missing")
    if seconds <= 0:
        return {"status": "skipped"}
    with tempfile.TemporaryDirectory(prefix="portable-release-cwd-") as cwd:
        process = subprocess.Popen([str(executable)], cwd=cwd)
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                code = process.poll()
                if code is not None:
                    raise PortableVerificationError(f"portable executable exited during smoke test: {code}")
                time.sleep(0.25)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    return {"status": "alive", "seconds": seconds}


def verify_portable_release(bundle_root: str | Path, *, exe_smoke_seconds: float = 12.0) -> dict[str, Any]:
    bundle = Path(bundle_root).resolve()
    release_path = bundle / "release_manifest.json"
    if not release_path.is_file():
        raise PortableVerificationError("release_manifest.json is missing")
    release = json.loads(release_path.read_text(encoding="utf-8"))
    inventory = _verify_inventory(bundle, release)
    _verify_no_secrets_or_absolute_sources(bundle)
    rag = _verify_rag(bundle, release)
    semantic = _verify_semantic_model(bundle, release, Path(rag.pop("semantic_index")))
    node = _verify_node(bundle)
    executable = _verify_executable(bundle, exe_smoke_seconds)
    return {
        "status": "ready",
        "bundle": str(bundle),
        "version": str(release.get("version") or ""),
        "inventory": inventory,
        "rag": rag,
        "semantic_model": semantic,
        "node": node,
        "executable": executable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a complete extracted portable release")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--exe-smoke-seconds", type=float, default=12.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_portable_release(args.bundle, exe_smoke_seconds=args.exe_smoke_seconds)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, PortableVerificationError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
