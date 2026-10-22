"""Bounded NPZ reader used exclusively by the one-time Qdrant migration."""
from __future__ import annotations
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, BinaryIO
import numpy as np
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
