"""Optional NumPy cosine retrieval over the local embedding sidecar."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from .contracts import SemanticRetrievalResult, StageStatus, write_json


MAX_ARCHIVE_MEMBERS = 16
MAX_ARCHIVE_EXPANDED_BYTES = 768 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 640 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250.0
MAX_SEMANTIC_COUNT = 1_000_000
MAX_SEMANTIC_DIMENSION = 8_192
MAX_VECTOR_BYTES = 512 * 1024 * 1024
MAX_STRING_ARRAY_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
_REQUIRED_ARRAY_MEMBERS = {
    "vectors": "vectors.npy",
    "chunk_ids": "chunk_ids.npy",
    "content_hashes": "content_hashes.npy",
}


def _readonly_sqlite(path: str | Path) -> sqlite3.Connection:
    target = Path(path).resolve()
    connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _file_fingerprint(path: str | Path) -> tuple[str, int, int]:
    target = Path(path).resolve()
    stat = target.stat()
    return str(target), int(stat.st_size), int(stat.st_mtime_ns)


def _npy_header(archive: zipfile.ZipFile, member: str) -> tuple[tuple[int, ...], bool, np.dtype[Any]]:
    with archive.open(member, "r") as stream:
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            shape, fortran_order, dtype = np.lib.format.read_array_header_1_0(stream)
        elif version in {(2, 0), (3, 0)}:
            shape, fortran_order, dtype = np.lib.format.read_array_header_2_0(stream)
        else:
            raise ValueError("semantic_index_npy_version_unsupported")
    normalized_shape = tuple(int(value) for value in shape)
    if any(value < 0 for value in normalized_shape):
        raise ValueError("semantic_index_shape_invalid")
    return normalized_shape, bool(fortran_order), np.dtype(dtype)


def _array_nbytes(shape: tuple[int, ...], dtype: np.dtype[Any]) -> int:
    count = 1
    for value in shape:
        count *= value
    return count * int(dtype.itemsize)


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
    stream.seek(0)
    return digest.hexdigest()


def load_semantic_manifest(
    path: str | Path,
    *,
    expected_sha256: str = "",
) -> tuple[dict[str, Any], str]:
    """Read a bounded JSON manifest and bind parsing to the checked bytes."""
    target = Path(path).resolve()
    with target.open("rb") as stream:
        payload = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("semantic_manifest_size_limit")
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    expected = str(expected_sha256 or "").strip().casefold()
    if expected and actual_sha256.casefold() != expected:
        raise ValueError("semantic_manifest_sha256_mismatch")
    try:
        loaded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("semantic_manifest_invalid") from exc
    if not isinstance(loaded, dict):
        raise ValueError("semantic_manifest_invalid")
    return loaded, actual_sha256


def preflight_semantic_npz(stream: BinaryIO) -> dict[str, tuple[tuple[int, ...], np.dtype[Any]]]:
    try:
        stream.seek(0)
        with zipfile.ZipFile(stream, "r") as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("semantic_index_member_limit")
            names = [item.filename for item in members]
            if len(names) != len(set(names)):
                raise ValueError("semantic_index_duplicate_member")
            required = set(_REQUIRED_ARRAY_MEMBERS.values())
            if not required.issubset(names):
                raise ValueError("semantic_index_arrays_missing")
            expanded = 0
            for item in members:
                size = int(item.file_size)
                compressed = int(item.compress_size)
                if size < 0 or compressed < 0 or size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise ValueError("semantic_index_member_size_limit")
                expanded += size
                if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise ValueError("semantic_index_expanded_size_limit")
                if size and (compressed <= 0 or size / compressed > MAX_COMPRESSION_RATIO):
                    raise ValueError("semantic_index_compression_ratio_limit")

            headers: dict[str, tuple[tuple[int, ...], np.dtype[Any]]] = {}
            for key, member in _REQUIRED_ARRAY_MEMBERS.items():
                shape, _fortran_order, dtype = _npy_header(archive, member)
                if dtype.hasobject or dtype.kind == "O":
                    raise ValueError("semantic_index_object_dtype_unsafe")
                headers[key] = (shape, dtype)
    except zipfile.BadZipFile as exc:
        raise ValueError("semantic_index_archive_invalid") from exc

    vector_shape, vector_dtype = headers["vectors"]
    chunk_shape, chunk_dtype = headers["chunk_ids"]
    hash_shape, hash_dtype = headers["content_hashes"]
    if len(vector_shape) != 2 or len(chunk_shape) != 1 or len(hash_shape) != 1:
        raise ValueError("semantic_index_shape_invalid")
    count, dimension = vector_shape
    if count <= 0 or count > MAX_SEMANTIC_COUNT:
        raise ValueError("semantic_index_count_limit")
    if dimension <= 0 or dimension > MAX_SEMANTIC_DIMENSION:
        raise ValueError("semantic_index_dimension_limit")
    if chunk_shape[0] != count or hash_shape[0] != count:
        raise ValueError("semantic_index_count_mismatch")
    if vector_dtype.kind != "f" or vector_dtype.itemsize not in {2, 4, 8}:
        raise ValueError("semantic_index_vector_dtype_invalid")
    vector_bytes = _array_nbytes(vector_shape, vector_dtype)
    converted_vector_bytes = count * dimension * np.dtype(np.float32).itemsize
    if max(vector_bytes, converted_vector_bytes) > MAX_VECTOR_BYTES:
        raise ValueError("semantic_index_vector_size_limit")
    if chunk_dtype.kind not in {"U", "S"} or hash_dtype.kind not in {"U", "S"}:
        raise ValueError("semantic_index_string_dtype_invalid")
    string_bytes = _array_nbytes(chunk_shape, chunk_dtype) + _array_nbytes(hash_shape, hash_dtype)
    if string_bytes > MAX_STRING_ARRAY_BYTES:
        raise ValueError("semantic_index_string_size_limit")
    return headers


def load_semantic_arrays(
    path: str | Path,
    *,
    expected_sha256: str = "",
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    """Safely validate and load the bounded semantic arrays from one file handle."""
    target = Path(path).resolve()
    with target.open("rb") as index_stream:
        actual_sha256 = _sha256_stream(index_stream)
        expected = str(expected_sha256 or "").strip().casefold()
        if expected and actual_sha256.casefold() != expected:
            raise ValueError("semantic_index_sha256_mismatch")
        preflight_semantic_npz(index_stream)
        index_stream.seek(0)
        with np.load(index_stream, allow_pickle=False) as data:
            vectors = np.asarray(data["vectors"])
            chunk_ids = np.asarray(data["chunk_ids"])
            content_hashes = np.asarray(data["content_hashes"])
            if any(array.dtype.hasobject or array.dtype.kind == "O" for array in (vectors, chunk_ids, content_hashes)):
                raise ValueError("semantic_index_object_dtype_unsafe")
            if vectors.dtype.kind != "f" or vectors.dtype.itemsize not in {2, 4, 8}:
                raise ValueError("semantic_index_vector_dtype_invalid")
            if not bool(np.isfinite(vectors).all()):
                raise ValueError("semantic_index_vector_non_finite")
            return vectors, chunk_ids, content_hashes


def _db_retrieval_fingerprint(db: sqlite3.Connection) -> str:
    rows = db.execute("SELECT chunk_id,content_hash FROM pipeline_chunks WHERE retrieval_role='retrieval' AND text_search<>'' ORDER BY document_id,location,chunk_id").fetchall()
    payload = [(str(row[0]), str(row[1] or "")) for row in rows]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _packaged_model_path(model_name: str) -> Path | None:
    if bool(getattr(sys, "frozen", False)):
        resource_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
    else:
        from runtime.app_paths import application_resource_root

        resource_root = application_resource_root().resolve()
    manifest_path = resource_root / "embedding_models" / "model_manifest.json"
    if not manifest_path.is_file():
        if bool(getattr(sys, "frozen", False)):
            raise RuntimeError("冻结程序缺少本地语义模型清单")
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("发布包的本地语义模型清单无效") from exc
    packaged_name = str(payload.get("model_name") or "").strip()
    if packaged_name != str(model_name or "").strip():
        raise RuntimeError("发布包的本地语义模型与活动索引不匹配")
    relative = str(payload.get("relative_path") or "").strip()
    if not relative:
        raise RuntimeError("发布包的本地语义模型路径缺失")
    candidate = (resource_root / relative).resolve()
    try:
        candidate.relative_to(resource_root)
    except ValueError as exc:
        raise RuntimeError("发布包的本地语义模型路径越界") from exc
    if not candidate.is_dir():
        raise RuntimeError("发布包缺少本地语义模型目录")
    return candidate


def _load_model(model_name: str, model_path: str | None = None) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("未安装 sentence-transformers，无法执行语义召回") from exc
    resolved = Path(model_path).expanduser() if str(model_path or "").strip() else _packaged_model_path(model_name)
    if resolved is not None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        return SentenceTransformer(str(resolved), local_files_only=True)
    return SentenceTransformer(str(resolved) if resolved is not None else model_name)


class SemanticRetriever:
    def __init__(
        self,
        index_path: str | Path,
        *,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        model_path: str | None = None,
        embedder: Any | None = None,
        expected_index_sha256: str = "",
        expected_manifest_sha256: str = "",
    ):
        self.index_path = Path(index_path).resolve()
        self.manifest_path = Path(str(self.index_path) + ".manifest.json")
        self.manifest = (
            load_semantic_manifest(
                self.manifest_path,
                expected_sha256=expected_manifest_sha256,
            )[0]
            if self.manifest_path.exists()
            else {}
        )
        vectors, chunk_ids, content_hashes = load_semantic_arrays(
            self.index_path,
            expected_sha256=expected_index_sha256,
        )
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.chunk_ids = [str(x) for x in chunk_ids.tolist()]
        self.content_hashes = [str(x) for x in content_hashes.tolist()]
        if (
            self.vectors.ndim != 2
            or len(self.chunk_ids) != self.vectors.shape[0]
            or len(self.content_hashes) != self.vectors.shape[0]
        ):
            raise ValueError("语义索引矩阵与 chunk_id 数量不一致")
        self._db_fingerprint_cache: dict[tuple[str, int, int], str] = {}
        self.embedder = embedder or _load_model(str(self.manifest.get("model_name") or model_name), model_path or self.manifest.get("model_path") or None)

    def search(self, query: str, db_path: str | Path, *, document_ids: list[str] | None = None, top_k: int = 20) -> list[dict[str, Any]]:
        ids = [str(x) for x in (document_ids or []) if str(x)]
        db_key = _file_fingerprint(db_path)
        with closing(_readonly_sqlite(db_path)) as db:
            db.row_factory = sqlite3.Row
            expected_fingerprint = str(self.manifest.get("source_fingerprint") or "")
            current_fingerprint = self._db_fingerprint_cache.get(db_key)
            if current_fingerprint is None:
                current_fingerprint = _db_retrieval_fingerprint(db)
                self._db_fingerprint_cache = {db_key: current_fingerprint}
            if expected_fingerprint and expected_fingerprint != current_fingerprint:
                raise RuntimeError("向量索引与当前 SQLite retrieval 语料不一致，请重建 semantic sidecar")
            valid: dict[str, sqlite3.Row] = {}
            for chunk_id, content_hash in zip(self.chunk_ids, self.content_hashes):
                row = db.execute("SELECT * FROM pipeline_chunks WHERE chunk_id=? AND retrieval_role='retrieval'", (chunk_id,)).fetchone()
                if not row or (ids and str(row["document_id"]) not in ids):
                    continue
                if content_hash and str(row["content_hash"] or "") != content_hash:
                    continue
                valid[chunk_id] = row
        values = self.embedder.encode([query], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        vector = np.asarray(values, dtype=np.float32).reshape(1, -1)
        vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
        if vector.shape[1] != self.vectors.shape[1]:
            raise RuntimeError(f"查询向量维度 {vector.shape[1]} 与索引维度 {self.vectors.shape[1]} 不一致")
        scores = self.vectors @ vector[0]
        order = np.argsort(-scores)
        results = []
        for rank, index in enumerate(order, 1):
            chunk_id = self.chunk_ids[int(index)]
            if chunk_id not in valid: continue
            results.append({"chunk_id": chunk_id, "semantic_score": float(scores[int(index)]), "semantic_rank": rank})
            if len(results) >= max(1, int(top_k)): break
        return results


def semantic_search(query: str, index_path: str | Path, db_path: str | Path, *, document_ids: list[str] | None = None, top_k: int = 20, embedder: Any | None = None) -> list[dict[str, Any]]:
    return SemanticRetriever(index_path, embedder=embedder).search(query, db_path, document_ids=document_ids, top_k=top_k)


def _semantic_scope_available(db_path: str | Path, document_ids: list[str] | None = None) -> bool:
    ids = [str(item) for item in (document_ids or []) if str(item)]
    with closing(_readonly_sqlite(db_path)) as db:
        if ids:
            placeholders = ",".join("?" for _ in ids)
            count = db.execute(
                f"SELECT COUNT(*) FROM pipeline_chunks WHERE retrieval_role='retrieval' AND document_id IN ({placeholders})",
                ids,
            ).fetchone()[0]
        else:
            count = db.execute(
                "SELECT COUNT(*) FROM pipeline_chunks WHERE retrieval_role='retrieval'"
            ).fetchone()[0]
    return bool(count)


def semantic_result(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    document_ids: list[str] | None = None,
    scope_available: bool = False,
    warning: str = "",
) -> SemanticRetrievalResult:
    warnings = [warning] if warning else []
    relevance_status = "unavailable" if warning else ("hit" if candidates else "no_hit")
    return SemanticRetrievalResult(
        query=query,
        candidates=[dict(item) for item in candidates],
        scope_available=bool(scope_available),
        retrieval_mode="semantic",
        relevance_status=relevance_status,
        scope_document_ids=[str(item) for item in (document_ids or []) if str(item)],
        status=StageStatus(
            status="failed" if warning else "ready",
            error=warning,
            warnings=warnings,
            stage_version="semantic-retrieve.v1",
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search local semantic embedding sidecar")
    parser.add_argument("query"); parser.add_argument("index", type=Path); parser.add_argument("db", type=Path); parser.add_argument("output", type=Path)
    parser.add_argument("--document-id", action="append", default=[]); parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args(argv)
    scope_available = False
    try:
        scope_available = _semantic_scope_available(args.db, args.document_id)
        result = semantic_search(args.query, args.index, args.db, document_ids=args.document_id, top_k=args.top_k)
        payload = semantic_result(
            args.query,
            result,
            document_ids=args.document_id,
            scope_available=scope_available,
        )
        exit_code = 0
    except Exception as exc:
        stable_reason = f"semantic_unavailable:{type(exc).__name__}"
        payload = semantic_result(
            args.query,
            [],
            document_ids=args.document_id,
            scope_available=scope_available,
            warning=stable_reason,
        )
        exit_code = 1
    write_json(str(args.output), payload.to_dict())
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
