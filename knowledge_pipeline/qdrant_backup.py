"""Export and restore native Qdrant snapshots; never overwrite a collection."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import quote

import httpx

from .qdrant_store import client_from_env, load_descriptor, qdrant_health, qdrant_url


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _headers() -> dict[str, str]:
    key = os.environ.get("YOLO11_QDRANT_API_KEY", "")
    return {"api-key": key} if key else {}


def export_snapshot(descriptor_path: str | Path, output: str | Path) -> dict[str, str]:
    descriptor, _ = load_descriptor(descriptor_path)
    target = Path(output)
    if target.exists():
        raise ValueError("qdrant_snapshot_output_exists")
    client = client_from_env()
    name = None
    temporary = target.with_name(target.name + ".tmp")
    try:
        health = qdrant_health(descriptor_path, client=client, verify_vectors=True)
        if not health["healthy"]:
            raise ValueError(health["reason"])
        collection = descriptor["collection_name"]
        snapshot = client.create_snapshot(collection, wait=True)
        name = snapshot.name
        url = f"{qdrant_url().rstrip('/')}/collections/{quote(collection, safe='')}/snapshots/{quote(name, safe='')}"
        target.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", url, headers=_headers(), timeout=120) as response:
            response.raise_for_status()
            with temporary.open("wb") as stream:
                for block in response.iter_bytes():
                    stream.write(block)
        checksum = file_sha256(temporary)
        advertised = getattr(snapshot, "checksum", None)
        if advertised and checksum != advertised:
            raise ValueError("qdrant_snapshot_sha256_mismatch")
        temporary.replace(target)
        return {"collection_name": collection, "sha256": checksum}
    finally:
        temporary.unlink(missing_ok=True)
        try:
            if name:
                client.delete_snapshot(descriptor["collection_name"], name, wait=True)
        finally:
            client.close()


def restore_snapshot(descriptor_path: str | Path, snapshot_path: str | Path, *, expected_sha256: str) -> dict[str, str]:
    descriptor, _ = load_descriptor(descriptor_path)
    snapshot = Path(snapshot_path)
    if not expected_sha256 or file_sha256(snapshot) != expected_sha256:
        raise ValueError("qdrant_snapshot_sha256_mismatch")
    collection = descriptor["collection_name"]
    client = client_from_env()
    try:
        if client.collection_exists(collection):
            health = qdrant_health(descriptor_path, client=client, verify_vectors=True)
            if not health["healthy"]:
                raise ValueError("qdrant_restore_existing_collection_mismatch")
            return {"status": "already_restored", "collection_name": collection}
        url = f"{qdrant_url().rstrip('/')}/collections/{quote(collection, safe='')}/snapshots/upload"
        with snapshot.open("rb") as stream:
            response = httpx.post(url, params={"wait": "true", "priority": "snapshot"},
                                  files={"snapshot": (snapshot.name, stream, "application/octet-stream")},
                                  headers=_headers(), timeout=120)
        response.raise_for_status()
        health = qdrant_health(descriptor_path, client=client, verify_vectors=True)
        if not health["healthy"]:
            raise ValueError(f"qdrant_restore_validation_failed:{health['reason']}")
        return {"status": "ready", "collection_name": collection}
    finally:
        client.close()
