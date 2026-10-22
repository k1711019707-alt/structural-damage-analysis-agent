from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from knowledge_pipeline.qdrant_store import QdrantSemanticRetriever, sync_npz_to_qdrant


class _FakeEmbedder:
    def encode(self, texts, **kwargs):
        return [[1.0, 0.0] if "裂缝" in text else [0.0, 1.0] for text in texts]


def _db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE pipeline_chunks (chunk_id TEXT, document_id TEXT, content_hash TEXT, retrieval_role TEXT, text_search TEXT, location TEXT DEFAULT '')")
        db.executemany(
            "INSERT INTO pipeline_chunks(chunk_id,document_id,content_hash,retrieval_role,text_search) VALUES (?, ?, ?, 'retrieval', ?)",
            [("c0", "doc-a", "h0", "梁体裂缝"), ("c1", "doc-b", "h1", "柱脚剥落")],
        )


def test_sync_and_scoped_qdrant_retrieval(tmp_path: Path) -> None:
    from qdrant_client import QdrantClient

    db = tmp_path / "knowledge.sqlite3"
    _db(db)
    client = QdrantClient(":memory:")
    npz = tmp_path / "semantic.npz"
    np.savez_compressed(
        npz,
        vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        chunk_ids=np.asarray(["c0", "c1"]),
        content_hashes=np.asarray(["h0", "h1"]),
    )
    descriptor_path = tmp_path / "qdrant.json"
    descriptor = sync_npz_to_qdrant(npz, descriptor_path, db_path=db, collection_name="test-corpus", client=client)
    assert descriptor["count"] == 2
    retriever = QdrantSemanticRetriever(descriptor_path, embedder=_FakeEmbedder(), client=client)
    result = retriever.search("梁体裂缝", db, document_ids=["doc-a"], top_k=1)
    assert result[0]["chunk_id"] == "c0"
    assert result[0]["semantic_score"] > 0.9


def test_descriptor_rejects_changed_sqlite_corpus(tmp_path: Path) -> None:
    from qdrant_client import QdrantClient

    db = tmp_path / "knowledge.sqlite3"
    _db(db)
    client = QdrantClient(":memory:")
    npz = tmp_path / "semantic.npz"
    np.savez_compressed(npz, vectors=np.eye(2, dtype=np.float32), chunk_ids=np.asarray(["c0", "c1"]), content_hashes=np.asarray(["h0", "h1"]))
    descriptor_path = tmp_path / "qdrant.json"
    sync_npz_to_qdrant(npz, descriptor_path, db_path=db, collection_name="test-corpus", client=client)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE pipeline_chunks SET content_hash='changed' WHERE chunk_id='c0'")
    retriever = QdrantSemanticRetriever(descriptor_path, embedder=_FakeEmbedder(), client=client)
    with pytest.raises(RuntimeError, match="Qdrant collection"):
        retriever.search("梁体裂缝", db)


def test_scope_filter_applies_before_top_k(tmp_path: Path) -> None:
    from qdrant_client import QdrantClient
    from knowledge_pipeline.qdrant_store import source_fingerprint, sync_vectors

    db = tmp_path / "knowledge.sqlite3"
    _db(db)
    client = QdrantClient(":memory:")
    descriptor = tmp_path / "qdrant.json"
    sync_vectors(np.eye(2, dtype=np.float32), ["c0", "c1"], ["h0", "h1"],
                 document_ids=["doc-a", "doc-b"], collection_name="scope", model_name="fake",
                 source_fingerprint_value=source_fingerprint([("c0", "h0"), ("c1", "h1")]),
                 descriptor_path=descriptor, client=client)
    retriever = QdrantSemanticRetriever(descriptor, embedder=_FakeEmbedder(), client=client)
    # The selected document has a lower vector score than the excluded document.
    result = retriever.search("裂缝", db, document_ids=["doc-b"], top_k=1)
    assert [item["chunk_id"] for item in result] == ["c1"]


def test_integrity_detects_vector_mutation(tmp_path: Path) -> None:
    from qdrant_client import QdrantClient, models
    from knowledge_pipeline.qdrant_store import point_id, qdrant_health, sync_vectors

    client = QdrantClient(":memory:")
    descriptor = tmp_path / "qdrant.json"
    sync_vectors(np.eye(2, dtype=np.float32), ["c0", "c1"], ["h0", "h1"],
                 document_ids=["doc-a", "doc-b"], collection_name="integrity", model_name="fake",
                 source_fingerprint_value="fingerprint", descriptor_path=descriptor, client=client)
    assert qdrant_health(descriptor, client=client, verify_vectors=True)["healthy"]
    client.update_vectors("integrity", [models.PointVectors(id=point_id("c0"), vector=[0.0, 1.0])], wait=True)
    assert qdrant_health(descriptor, client=client, verify_vectors=True)["reason"] == "qdrant_vectors_sha256_mismatch"


def test_sync_failure_does_not_publish_descriptor(tmp_path: Path) -> None:
    from qdrant_client import QdrantClient
    from knowledge_pipeline.qdrant_store import sync_vectors

    client = QdrantClient(":memory:")
    descriptor = tmp_path / "qdrant.json"
    with pytest.raises(ValueError, match="non_finite"):
        sync_vectors(np.asarray([[np.nan, 0]]), ["c0"], ["h0"], document_ids=["doc-a"],
                     collection_name="invalid", model_name="fake", source_fingerprint_value="x",
                     descriptor_path=descriptor, client=client)
    assert not descriptor.exists()


def test_repeat_sync_is_idempotent_and_rejects_published_vector_changes(tmp_path: Path, qdrant_client) -> None:
    from knowledge_pipeline.qdrant_store import sync_vectors

    descriptor = tmp_path / "qdrant.json"
    kwargs = dict(document_ids=["doc-a", "doc-b"], collection_name="retry", model_name="fake",
                  source_fingerprint_value="fingerprint", descriptor_path=descriptor, client=qdrant_client)
    first = sync_vectors(np.eye(2), ["c0", "c1"], ["h0", "h1"], **kwargs)
    before = descriptor.read_bytes()
    second = sync_vectors(np.eye(2), ["c0", "c1"], ["h0", "h1"], **kwargs)
    assert first == second
    assert descriptor.read_bytes() == before
    assert qdrant_client.count("retry").count == 2
    with pytest.raises(ValueError, match="qdrant_vector_upload_mismatch"):
        sync_vectors(np.flipud(np.eye(2)), ["c0", "c1"], ["h0", "h1"], **kwargs)
    assert descriptor.read_bytes() == before


def test_embedding_writes_only_qdrant_metadata(tmp_path: Path, qdrant_client) -> None:
    from knowledge_pipeline.embed import build_embedding_index

    database = tmp_path / "knowledge.sqlite3"
    _db(database)
    descriptor = tmp_path / "semantic.qdrant.json"
    metadata = build_embedding_index(database, descriptor, embedder=_FakeEmbedder(), client=qdrant_client)
    assert metadata["backend"] == "qdrant"
    assert qdrant_client.count(metadata["collection_name"]).count == 2
    assert not list(tmp_path.glob("*.npz"))
    with pytest.raises(ValueError, match="不再生成 NPZ"):
        build_embedding_index(database, tmp_path / "semantic.npz", embedder=_FakeEmbedder(), client=qdrant_client)


def test_failed_migration_preserves_active_manifest(tmp_path: Path, monkeypatch) -> None:
    import json
    import scripts.sync_qdrant as migration

    database = tmp_path / "knowledge.sqlite3"
    _db(database)
    source = tmp_path / "semantic.npz"
    source.write_bytes(b"legacy")
    manifest = tmp_path / "active_rag.json"
    manifest.write_text(json.dumps({"database_path": database.name, "semantic_index_path": source.name}), encoding="utf-8")
    before = manifest.read_bytes()
    monkeypatch.setattr(migration, "load_semantic_manifest", lambda *a, **kw: ({"model_name": "fake"}, "digest"))

    def fail(*args, **kwargs):
        raise RuntimeError("qdrant_upload_failed")

    monkeypatch.setattr(migration, "sync_npz_to_qdrant", fail)
    with pytest.raises(RuntimeError, match="qdrant_upload_failed"):
        migration.migrate_active(manifest)
    assert manifest.read_bytes() == before


def test_query_rejects_payload_identity_tampering(tmp_path: Path, qdrant_client) -> None:
    from knowledge_pipeline.embed import build_embedding_index
    from knowledge_pipeline.qdrant_store import point_id

    database = tmp_path / "knowledge.sqlite3"
    _db(database)
    descriptor = tmp_path / "semantic.qdrant.json"
    metadata = build_embedding_index(database, descriptor, embedder=_FakeEmbedder(), client=qdrant_client)
    qdrant_client.set_payload(metadata["collection_name"], {"document_id": "wrong"}, points=[point_id("c0")])
    retriever = QdrantSemanticRetriever(descriptor, embedder=_FakeEmbedder(), client=qdrant_client)
    with pytest.raises(RuntimeError, match="qdrant_corpus_identity_mismatch"):
        retriever.search("裂缝", database)
