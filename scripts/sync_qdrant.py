"""One-time NPZ migration with strict verification before activation."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.qdrant_store import sync_npz_to_qdrant
from knowledge_pipeline.legacy_npz import load_semantic_manifest
from runtime.rag_production import _ExclusiveFileLock, atomic_write_json, inspect_v2_database, sha256_file
from runtime.app_paths import active_rag_manifest_path, resolve_knowledge_portable_path


def migrate_active(manifest_path: Path) -> dict[str, object]:
    manifest_path = manifest_path.resolve()
    with _ExclusiveFileLock(manifest_path.parent / ".activation.lock"):
        active = json.loads(manifest_path.read_text(encoding="utf-8"))
        before_sha = sha256_file(manifest_path)
        source = resolve_knowledge_portable_path(active.get("semantic_index_path"), root=manifest_path.parent)
        database = resolve_knowledge_portable_path(active.get("database_path"), root=manifest_path.parent)
        if source is None or database is None:
            raise ValueError("active_rag_paths_invalid")
        if source.suffix.lower() == ".json":
            health = inspect_v2_database(
                database, semantic_index_path=source, expected_sha256=active["database_sha256"],
                expected_semantic_index_sha256=active.get("semantic_index_sha256"),
                expected_semantic_manifest_sha256=active.get("semantic_manifest_sha256"), strict=True,
            )
            if not health.get("healthy"):
                raise ValueError(str(health.get("reason")))
            return {"status": "already_migrated", "descriptor": str(source)}
        metadata, _ = load_semantic_manifest(str(source) + ".manifest.json", expected_sha256=str(active.get("semantic_manifest_sha256") or ""))
        target = source.with_name("semantic.qdrant.json")
        collection = f"knowledge_migration_{uuid.uuid4().hex}"
        descriptor = sync_npz_to_qdrant(
            source, target, db_path=database, collection_name=collection,
            model_name=str(metadata.get("model_name") or "BAAI/bge-small-zh-v1.5"),
            expected_index_sha256=str(active.get("semantic_index_sha256") or ""),
        )
        health = inspect_v2_database(database, semantic_index_path=target, expected_sha256=active["database_sha256"], strict=True)
        if not health.get("healthy"):
            raise ValueError(f"qdrant_migration_validation_failed:{health.get('reason')}")
        if sha256_file(manifest_path) != before_sha:
            raise ValueError("active_manifest_changed_during_migration")
        updated = dict(active)
        semantic = health["semantic"]
        updated.update({
            "semantic_backend": "qdrant",
            "semantic_index_path": target.relative_to(manifest_path.parent).as_posix(),
            "semantic_index_sha256": semantic["index_sha256"],
            "semantic_manifest_path": Path(str(target.relative_to(manifest_path.parent)) + ".manifest.json").as_posix(),
            "semantic_manifest_sha256": semantic["manifest_sha256"],
            "vector_migration": {"backend": "qdrant", "collection_name": collection, "verified": True, "count": descriptor["count"]},
        })
        evidence = dict(updated.get("validation_evidence") or {})
        evidence.update({"semantic_index_sha256": semantic["index_sha256"], "semantic_manifest_sha256": semantic["manifest_sha256"]})
        updated["validation_evidence"] = evidence
        atomic_write_json(manifest_path, updated)
        return {"status": "ready", "collection_name": collection, "count": descriptor["count"], "dimension": descriptor["dimension"], "descriptor": str(target), "active_manifest": str(manifest_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate the active semantic NPZ to local Qdrant without re-embedding")
    parser.add_argument("--active-manifest", type=Path, default=active_rag_manifest_path())
    args = parser.parse_args()
    print(json.dumps(migrate_active(args.active_manifest), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
