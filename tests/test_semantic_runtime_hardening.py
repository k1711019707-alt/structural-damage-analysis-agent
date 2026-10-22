from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from knowledge_pipeline import legacy_npz as semantic_retrieve
from knowledge_pipeline.qdrant_store import readonly_sqlite

# Exercise the migration reader, without exposing it as a runtime retriever.
def _load_migration_vectors(path, *, embedder=None, expected_index_sha256="", expected_manifest_sha256=""):
    from types import SimpleNamespace
    semantic_retrieve.load_semantic_manifest(str(path) + ".manifest.json", expected_sha256=expected_manifest_sha256)
    vectors, _, _ = semantic_retrieve.load_semantic_arrays(path, expected_sha256=expected_index_sha256)
    return SimpleNamespace(vectors=vectors)



def _write_manifest(path: Path, *, count: int, dimension: int) -> None:
    Path(str(path) + ".manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "knowledge-embeddings.v1",
                "model_name": "fake",
                "count": count,
                "dimension": dimension,
            }
        ),
        encoding="utf-8",
    )


def _write_sidecar(path: Path, *, vectors: np.ndarray, chunk_ids: np.ndarray, hashes: np.ndarray) -> None:
    np.savez_compressed(path, vectors=vectors, chunk_ids=chunk_ids, content_hashes=hashes)
    _write_manifest(path, count=int(vectors.shape[0]), dimension=int(vectors.shape[1]))


class _FakeEmbedder:
    def encode(self, _texts: list[str], **_kwargs: object) -> list[list[float]]:
        return [[1.0, 0.0]]


def test_semantic_loader_rejects_object_dtype_before_numpy_materialization(tmp_path: Path) -> None:
    sidecar = tmp_path / "object.npz"
    _write_sidecar(
        sidecar,
        vectors=np.asarray([[1.0, 0.0]], dtype=np.float32),
        chunk_ids=np.asarray([{"unsafe": "value"}], dtype=object),
        hashes=np.asarray(["hash"], dtype=np.str_),
    )

    with pytest.raises(ValueError, match="semantic_index_object_dtype_unsafe"):
        _load_migration_vectors(sidecar, embedder=_FakeEmbedder())


@pytest.mark.parametrize(
    "dtype",
    [np.int32, np.complex64],
)
def test_semantic_loader_rejects_non_float_vector_dtypes(
    tmp_path: Path, dtype: type[np.generic]
) -> None:
    sidecar = tmp_path / f"vectors-{np.dtype(dtype).name}.npz"
    _write_sidecar(
        sidecar,
        vectors=np.asarray([[1, 0]], dtype=dtype),
        chunk_ids=np.asarray(["chunk"], dtype=np.str_),
        hashes=np.asarray(["hash"], dtype=np.str_),
    )

    with pytest.raises(ValueError, match="semantic_index_vector_dtype_invalid"):
        _load_migration_vectors(sidecar, embedder=_FakeEmbedder())


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_semantic_loader_rejects_non_finite_vectors(tmp_path: Path, invalid: float) -> None:
    sidecar = tmp_path / "non-finite.npz"
    _write_sidecar(
        sidecar,
        vectors=np.asarray([[1.0, invalid]], dtype=np.float32),
        chunk_ids=np.asarray(["chunk"], dtype=np.str_),
        hashes=np.asarray(["hash"], dtype=np.str_),
    )

    with pytest.raises(ValueError, match="semantic_index_vector_non_finite"):
        _load_migration_vectors(sidecar, embedder=_FakeEmbedder())


def test_semantic_loader_binds_npz_and_manifest_hashes(tmp_path: Path) -> None:
    import hashlib

    sidecar = tmp_path / "digest-bound.npz"
    _write_sidecar(
        sidecar,
        vectors=np.asarray([[1.0, 0.0]], dtype=np.float32),
        chunk_ids=np.asarray(["chunk"], dtype=np.str_),
        hashes=np.asarray(["hash"], dtype=np.str_),
    )
    manifest = Path(str(sidecar) + ".manifest.json")
    index_sha256 = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    retriever = _load_migration_vectors(
        sidecar,
        embedder=_FakeEmbedder(),
        expected_index_sha256=index_sha256,
        expected_manifest_sha256=manifest_sha256,
    )
    assert retriever.vectors.shape == (1, 2)

    with pytest.raises(ValueError, match="semantic_index_sha256_mismatch"):
        _load_migration_vectors(
            sidecar,
            embedder=_FakeEmbedder(),
            expected_index_sha256="0" * 64,
            expected_manifest_sha256=manifest_sha256,
        )
    with pytest.raises(ValueError, match="semantic_manifest_sha256_mismatch"):
        _load_migration_vectors(
            sidecar,
            embedder=_FakeEmbedder(),
            expected_index_sha256=index_sha256,
            expected_manifest_sha256="0" * 64,
        )


def test_semantic_loader_bounds_manifest_before_json_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar = tmp_path / "large-manifest.npz"
    _write_sidecar(
        sidecar,
        vectors=np.asarray([[1.0, 0.0]], dtype=np.float32),
        chunk_ids=np.asarray(["chunk"], dtype=np.str_),
        hashes=np.asarray(["hash"], dtype=np.str_),
    )
    manifest = Path(str(sidecar) + ".manifest.json")
    manifest.write_bytes(b"{" + (b" " * 64) + b"}")
    monkeypatch.setattr(semantic_retrieve, "MAX_MANIFEST_BYTES", 16)

    with pytest.raises(ValueError, match="semantic_manifest_size_limit"):
        _load_migration_vectors(sidecar, embedder=_FakeEmbedder())


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "expected"),
    [
        ("MAX_VECTOR_BYTES", 1, "semantic_index_vector_size_limit"),
        ("MAX_STRING_ARRAY_BYTES", 1, "semantic_index_string_size_limit"),
        ("MAX_ARCHIVE_EXPANDED_BYTES", 1, "semantic_index_expanded_size_limit"),
        ("MAX_COMPRESSION_RATIO", 1.0, "semantic_index_compression_ratio_limit"),
    ],
)
def test_semantic_loader_enforces_resource_limits_before_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit_value: int | float,
    expected: str,
) -> None:
    sidecar = tmp_path / f"{limit_name}.npz"
    _write_sidecar(
        sidecar,
        vectors=np.zeros((8, 64), dtype=np.float32),
        chunk_ids=np.asarray([f"chunk-{index}" for index in range(8)], dtype=np.str_),
        hashes=np.asarray(["0" * 64 for _ in range(8)], dtype=np.str_),
    )
    monkeypatch.setattr(semantic_retrieve, limit_name, limit_value)

    with pytest.raises(ValueError, match=expected):
        _load_migration_vectors(sidecar, embedder=_FakeEmbedder())


def test_semantic_loader_rejects_count_and_dimension_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = tmp_path / "shape.npz"
    _write_sidecar(
        sidecar,
        vectors=np.ones((2, 3), dtype=np.float32),
        chunk_ids=np.asarray(["a", "b"], dtype=np.str_),
        hashes=np.asarray(["ha", "hb"], dtype=np.str_),
    )
    monkeypatch.setattr(semantic_retrieve, "MAX_SEMANTIC_COUNT", 1)
    with pytest.raises(ValueError, match="semantic_index_count_limit"):
        _load_migration_vectors(sidecar, embedder=_FakeEmbedder())

    monkeypatch.setattr(semantic_retrieve, "MAX_SEMANTIC_COUNT", 10)
    monkeypatch.setattr(semantic_retrieve, "MAX_SEMANTIC_DIMENSION", 2)
    with pytest.raises(ValueError, match="semantic_index_dimension_limit"):
        _load_migration_vectors(sidecar, embedder=_FakeEmbedder())


def test_readonly_sqlite_helpers_reject_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "readonly.sqlite3"
    with sqlite3.connect(db_path) as writable:
        writable.execute("CREATE TABLE sample(value TEXT)")
        writable.execute("INSERT INTO sample(value) VALUES ('ready')")

    from knowledge_pipeline.retrieve import _readonly_sqlite as retrieve_readonly
    from runtime.knowledge_base import _readonly_sqlite as runtime_readonly

    for factory in (readonly_sqlite, retrieve_readonly, runtime_readonly):
        with factory(db_path) as readonly:
            assert readonly.execute("PRAGMA query_only").fetchone()[0] == 1
            assert readonly.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            assert readonly.execute("SELECT value FROM sample").fetchone()[0] == "ready"
            with pytest.raises(sqlite3.OperationalError):
                readonly.execute("INSERT INTO sample(value) VALUES ('blocked')")


def test_production_semantic_health_rejects_legacy_npz(tmp_path: Path) -> None:
    from runtime import rag_production
    import sqlite3

    sidecar = tmp_path / "legacy.npz"
    _write_sidecar(sidecar, vectors=np.ones((1, 2), dtype=np.float32),
                   chunk_ids=np.asarray(["chunk"]), hashes=np.asarray(["hash"]))
    with sqlite3.connect(":memory:") as db:
        health = rag_production._semantic_health(sidecar, db)
    assert not health["healthy"]
    assert health["reason"] == "semantic_qdrant_descriptor_required"


def test_knowledge_base_read_apis_use_query_only_connections(tmp_path: Path) -> None:
    from runtime.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(tmp_path / "legacy")
    folder = kb.create_folder("只读查询")
    with sqlite3.connect(kb.db_path) as writable:
        writable.execute(
            "INSERT INTO documents(document_id,path,name,extension,size_bytes,modified_ns,sha256,status,folder_id,error) "
            "VALUES ('doc-read','doc.pdf','doc.pdf','.pdf',1,1,'sha','ready',?,'')",
            (folder.folder_id,),
        )
        writable.execute("INSERT INTO chunks VALUES ('chunk-read','doc-read','page:1','裂缝检测')")
        writable.execute("INSERT INTO chunks_fts VALUES ('chunk-read','doc-read','page:1','裂缝检测')")

    assert kb.list_folders()[0].folder_id == folder.folder_id
    assert kb.list_documents()[0].document_id == "doc-read"
    assert kb.list_documents_in_folders([folder.folder_id])[0].document_id == "doc-read"
    assert kb.search("裂缝", document_ids=["doc-read"])[0].document_id == "doc-read"
    assert kb.search_with_scope("不存在词", document_ids=["doc-read"]).chunks
    from contextlib import closing

    with closing(kb._connect_readonly()) as readonly:
        assert readonly.execute("PRAGMA query_only").fetchone()[0] == 1
