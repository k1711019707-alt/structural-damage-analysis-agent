"""Persistent Qdrant vectors; SQLite remains authoritative for text and scope."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

import numpy as np

DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
DESCRIPTOR_SCHEMA = "knowledge-qdrant.v1"
MAX_DESCRIPTOR_BYTES = 1024 * 1024


def qdrant_url() -> str:
    return os.environ.get("YOLO11_QDRANT_URL", "").strip() or DEFAULT_QDRANT_URL


def client_from_env() -> Any:
    from qdrant_client import QdrantClient
    return QdrantClient(url=qdrant_url(), api_key=os.environ.get("YOLO11_QDRANT_API_KEY") or None, timeout=5)


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"yolo11-seg:{chunk_id}"))


def readonly_sqlite(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def source_fingerprint(rows: Iterable[tuple[Any, Any]]) -> str:
    identity = [(str(cid), str(digest or "")) for cid, digest in rows]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def retrieval_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT chunk_id,content_hash,document_id FROM pipeline_chunks "
        "WHERE retrieval_role='retrieval' AND text_search<>'' "
        "ORDER BY document_id,location,chunk_id"
    ).fetchall()


def load_descriptor(path: str | Path, *, expected_sha256: str = "") -> tuple[dict[str, Any], str]:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_DESCRIPTOR_BYTES + 1)
    if len(data) > MAX_DESCRIPTOR_BYTES:
        raise ValueError("semantic_manifest_size_limit")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("semantic_manifest_sha256_mismatch")
    value = json.loads(data)
    if not isinstance(value, dict) or value.get("schema_version") != DESCRIPTOR_SCHEMA:
        raise ValueError("semantic_qdrant_descriptor_required")
    for key in ("collection_name", "model_name", "source_fingerprint", "vectors_sha256"):
        if not str(value.get(key) or "").strip():
            raise ValueError("semantic_qdrant_descriptor_invalid")
    if int(value.get("dimension", 0)) <= 0 or int(value.get("count", 0)) <= 0 or value.get("status") != "ready":
        raise ValueError("semantic_qdrant_descriptor_invalid")
    if value.get("normalized") is not True or value.get("backend") != "qdrant":
        raise ValueError("semantic_qdrant_descriptor_invalid")
    return value, digest


def write_descriptor(path: str | Path, value: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(value, ensure_ascii=False, indent=2)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Both files contain bounded activation metadata, never vectors.
    for destination in (target, Path(str(target) + ".manifest.json")):
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(data, encoding="utf-8")
        os.replace(temporary, destination)
    return value


def collection_records(client: Any, collection: str, *, with_vectors: bool = True) -> list[Any]:
    records: list[Any] = []
    offset = None
    while True:
        batch, offset = client.scroll(collection_name=collection, limit=256, offset=offset, with_payload=True, with_vectors=with_vectors)
        records.extend(batch)
        if offset is None:
            return sorted(records, key=lambda point: str(point.id))


def records_digest(records: list[Any]) -> str:
    digest = hashlib.sha256()
    for record in records:
        payload = record.payload or {}
        identity = [str(record.id), payload.get("chunk_id"), payload.get("content_hash"), payload.get("document_id")]
        digest.update(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        vector = np.asarray(record.vector, dtype="<f4")
        if vector.ndim != 1 or not np.isfinite(vector).all():
            raise ValueError("qdrant_vectors_invalid")
        digest.update(vector.tobytes())
    return digest.hexdigest()


def _verify_records(records: list[Any], ids: list[str], hashes: list[str], docs: list[str], matrix: np.ndarray) -> None:
    expected = {point_id(cid): (i, cid, hashes[i], docs[i]) for i, cid in enumerate(ids)}
    if len(records) != len(expected):
        raise ValueError("qdrant_count_mismatch")
    for record in records:
        identity = expected.get(str(record.id))
        payload = record.payload or {}
        if identity is None or [payload.get(key) for key in ("chunk_id", "content_hash", "document_id")] != list(identity[1:]):
            raise ValueError("qdrant_corpus_identity_mismatch")
        if not np.allclose(np.asarray(record.vector), matrix[identity[0]], atol=1e-6, rtol=1e-5):
            raise ValueError("qdrant_vector_upload_mismatch")


def sync_vectors(vectors: np.ndarray, chunk_ids: Iterable[str], content_hashes: Iterable[str], *, document_ids: Iterable[str], collection_name: str, model_name: str, source_fingerprint_value: str, descriptor_path: str | Path, model_path: str | None = None, batch_size: int = 256, client: Any | None = None) -> dict[str, Any]:
    from qdrant_client import models
    matrix = np.asarray(vectors, dtype=np.float32)
    ids, hashes, docs = list(map(str, chunk_ids)), list(map(str, content_hashes)), list(map(str, document_ids))
    if matrix.ndim != 2 or not len(ids) or not (len(ids) == len(hashes) == len(docs) == matrix.shape[0]):
        raise ValueError("qdrant_count_mismatch")
    if len(set(ids)) != len(ids) or not all(ids) or not all(docs):
        raise ValueError("qdrant_identity_invalid")
    if not np.isfinite(matrix).all():
        raise ValueError("qdrant_vector_non_finite")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms <= 1e-12).any():
        raise ValueError("qdrant_zero_vector")
    matrix = matrix / norms
    qclient = client or client_from_env()
    target = Path(descriptor_path)
    if target.is_file():
        old, _ = load_descriptor(target)
        if old["collection_name"] == collection_name:
            if old["source_fingerprint"] != source_fingerprint_value or old["model_name"] != model_name:
                raise ValueError("qdrant_collection_already_published")
            records = collection_records(qclient, collection_name)
            _verify_records(records, ids, hashes, docs, matrix)
            if records_digest(records) != old["vectors_sha256"]:
                raise ValueError("qdrant_vectors_sha256_mismatch")
            return old
    if qclient.collection_exists(collection_name):
        info = qclient.get_collection(collection_name)
        if int(info.config.params.vectors.size) != matrix.shape[1] or info.config.params.vectors.distance != models.Distance.COSINE:
            raise ValueError("qdrant_collection_config_mismatch")
    else:
        qclient.create_collection(collection_name=collection_name, vectors_config=models.VectorParams(size=matrix.shape[1], distance=models.Distance.COSINE))
    size = max(1, int(batch_size))
    for start in range(0, len(ids), size):
        qclient.upsert(collection_name=collection_name, wait=True, points=[models.PointStruct(id=point_id(ids[i]), vector=matrix[i].tolist(), payload={"chunk_id": ids[i], "content_hash": hashes[i], "document_id": docs[i]}) for i in range(start, min(start + size, len(ids)))])
    records = collection_records(qclient, collection_name)
    _verify_records(records, ids, hashes, docs, matrix)
    return write_descriptor(target, {"schema_version": DESCRIPTOR_SCHEMA, "backend": "qdrant", "collection_name": collection_name, "dimension": matrix.shape[1], "count": len(ids), "model_name": model_name, "model_path": str(model_path or ""), "source_fingerprint": source_fingerprint_value, "vectors_sha256": records_digest(records), "normalized": True, "status": "ready"})


def sync_npz_to_qdrant(npz_path: str | Path, descriptor_path: str | Path, *, db_path: str | Path, collection_name: str, model_name: str = "BAAI/bge-small-zh-v1.5", expected_index_sha256: str = "", client: Any | None = None) -> dict[str, Any]:
    from .legacy_npz import load_semantic_arrays
    vectors, ids, hashes = load_semantic_arrays(npz_path, expected_sha256=expected_index_sha256)
    with closing(readonly_sqlite(db_path)) as db:
        rows = retrieval_rows(db)
    identity = [(str(row["chunk_id"]), str(row["content_hash"] or "")) for row in rows]
    if list(zip(map(str, ids.tolist()), map(str, hashes.tolist()))) != identity:
        raise ValueError("qdrant_migration_corpus_identity_mismatch")
    return sync_vectors(vectors, ids.tolist(), hashes.tolist(), document_ids=[row["document_id"] for row in rows], collection_name=collection_name, model_name=model_name, source_fingerprint_value=source_fingerprint(identity), descriptor_path=descriptor_path, client=client)


def qdrant_health(descriptor_path: str | Path, *, client: Any | None = None, verify_vectors: bool = False, db: sqlite3.Connection | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"configured": True, "healthy": False, "path": str(Path(descriptor_path).resolve())}
    qclient = client
    try:
        descriptor, _ = load_descriptor(descriptor_path)
        qclient = client or client_from_env()
        collection = descriptor["collection_name"]
        if not qclient.collection_exists(collection):
            return {**result, "reason": "qdrant_collection_missing"}
        info = qclient.get_collection(collection)
        from qdrant_client.models import Distance
        if info.config.params.vectors.distance != Distance.COSINE:
            return {**result, "reason": "qdrant_distance_mismatch"}
        if int(info.config.params.vectors.size) != int(descriptor["dimension"]):
            return {**result, "reason": "qdrant_dimension_mismatch"}
        if int(info.points_count or 0) != int(descriptor["count"]):
            return {**result, "reason": "qdrant_count_mismatch"}
        if db is not None:
            rows = retrieval_rows(db)
            identity = [(str(row["chunk_id"]), str(row["content_hash"] or "")) for row in rows]
            if source_fingerprint(identity) != descriptor["source_fingerprint"]:
                return {**result, "reason": "semantic_fingerprint_mismatch"}
        if verify_vectors:
            records = collection_records(qclient, collection, with_vectors=True)
            if db is not None:
                expected = {point_id(str(row["chunk_id"])): (str(row["chunk_id"]), str(row["content_hash"] or ""), str(row["document_id"])) for row in rows}
                actual = {str(record.id): tuple((record.payload or {}).get(key) for key in ("chunk_id", "content_hash", "document_id")) for record in records}
                if actual != expected:
                    return {**result, "reason": "qdrant_corpus_identity_mismatch"}
            if records_digest(records) != descriptor["vectors_sha256"]:
                return {**result, "reason": "qdrant_vectors_sha256_mismatch"}
        return {**result, "healthy": True, "manifest": descriptor, "retrieval_count": int(descriptor["count"]), "dimension": int(descriptor["dimension"])}
    except Exception as exc:
        reason = str(exc) if str(exc).startswith(("semantic_", "qdrant_")) else f"qdrant_unavailable:{type(exc).__name__}"
        return {**result, "reason": reason}
    finally:
        if client is None and qclient is not None:
            qclient.close()


class QdrantSemanticRetriever:
    def __init__(self, descriptor_path: str | Path, *, embedder: Any | None = None, model_name: str = "BAAI/bge-small-zh-v1.5", model_path: str | None = None, expected_index_sha256: str = "", expected_manifest_sha256: str = "", client: Any | None = None):
        self.index_path = Path(descriptor_path).resolve()
        self.manifest_path = Path(str(self.index_path) + ".manifest.json")
        self.manifest, digest = load_descriptor(self.index_path)
        if expected_index_sha256 and digest != expected_index_sha256:
            raise ValueError("semantic_index_sha256_mismatch")
        if expected_manifest_sha256:
            sibling, _ = load_descriptor(self.manifest_path, expected_sha256=expected_manifest_sha256)
            if sibling != self.manifest:
                raise ValueError("semantic_manifest_descriptor_mismatch")
        self.client = client or client_from_env()
        self.collection_name = self.manifest["collection_name"]
        from .semantic_retrieve import _load_model
        self.embedder = embedder or _load_model(self.manifest.get("model_name") or model_name, model_path or self.manifest.get("model_path") or None)

    def search(self, query: str, db_path: str | Path, *, document_ids: list[str] | None = None, top_k: int = 20) -> list[dict[str, Any]]:
        from qdrant_client import models
        with closing(readonly_sqlite(db_path)) as db:
            identity = [(str(row["chunk_id"]), str(row["content_hash"] or "")) for row in retrieval_rows(db)]
            if source_fingerprint(identity) != self.manifest["source_fingerprint"]:
                raise RuntimeError("Qdrant collection 与当前 SQLite retrieval 语料不一致")
            vector = np.asarray(self.embedder.encode([query], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False), dtype=np.float32).reshape(-1)
            if len(vector) != int(self.manifest["dimension"]) or not np.isfinite(vector).all() or float(np.linalg.norm(vector)) <= 1e-12:
                raise ValueError("qdrant_query_vector_invalid")
            ids = [str(item) for item in (document_ids or []) if str(item)]
            query_filter = models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchAny(any=ids))]) if ids else None
            response = self.client.query_points(collection_name=self.collection_name, query=vector.tolist(), query_filter=query_filter, limit=max(1, int(top_k)), with_payload=True, with_vectors=False)
            results = []
            for record in response.points:
                payload = record.payload or {}
                cid = str(payload.get("chunk_id") or "")
                row = db.execute("SELECT document_id,content_hash FROM pipeline_chunks WHERE chunk_id=? AND retrieval_role='retrieval' AND text_search<>''", (cid,)).fetchone()
                if row is None or str(record.id) != point_id(cid) or str(payload.get("document_id")) != str(row["document_id"]) or (ids and str(row["document_id"]) not in ids) or str(payload.get("content_hash") or "") != str(row["content_hash"] or ""):
                    raise RuntimeError("qdrant_corpus_identity_mismatch")
                results.append({"chunk_id": cid, "semantic_score": float(record.score), "semantic_rank": len(results) + 1})
            return results
