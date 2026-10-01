"""Build a local embedding sidecar for v2 retrieval children.

The model dependency is intentionally optional.  ``sentence-transformers`` is
loaded only when the CLI/API is used without an injected ``embedder``; tests
and deployments may provide any object exposing ``encode(texts, ...)``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from .contracts import StageStatus, read_json, write_json


def _fingerprint(rows: Iterable[sqlite3.Row]) -> str:
    payload = [(str(row["chunk_id"]), str(row["content_hash"] or "")) for row in rows]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _load_model(model_name: str, model_path: str | None = None, device: str | None = None) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("未安装 sentence-transformers，无法生成语义索引") from exc
    kwargs = {"device": str(device)} if str(device or "").strip() else {}
    return SentenceTransformer(model_path or model_name, **kwargs)


def _encode(embedder: Any, texts: list[str], batch_size: int) -> np.ndarray:
    values = embedder.encode(texts, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
    matrix = np.asarray(values, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != len(texts):
        raise ValueError("embedding 输出维度与文本数量不一致")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    return matrix


def build_embedding_index(
    db_path: str | Path,
    output_path: str | Path,
    *,
    model_name: str = "BAAI/bge-small-zh-v1.5",
    model_path: str | None = None,
    batch_size: int = 32,
    device: str | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    target = Path(output_path); target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM pipeline_chunks WHERE retrieval_role='retrieval' AND text_search<>'' ORDER BY document_id, location, chunk_id").fetchall()
        if not rows:
            raise ValueError("v2 数据库中没有 retrieval child")
        texts = [str(row["text_search"] or row["text"] or "") for row in rows]
        fingerprints = _fingerprint(rows)
        encoder = embedder or _load_model(model_name, model_path, device)
        matrix = _encode(encoder, texts, max(1, int(batch_size)))
        chunk_ids = np.asarray([str(row["chunk_id"]) for row in rows], dtype=np.str_)
        content_hashes = np.asarray([str(row["content_hash"] or "") for row in rows], dtype=np.str_)
    manifest = {
        "schema_version": "knowledge-embeddings.v1",
        "model_name": model_name,
        "model_path": str(model_path or ""),
        "device": str(device or "auto"),
        "dimension": int(matrix.shape[1]),
        "normalized": True,
        "count": int(matrix.shape[0]),
        "db_path": str(Path(db_path).resolve()),
        "source_fingerprint": fingerprints,
        "status": "ready",
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    np.savez_compressed(tmp, vectors=matrix, chunk_ids=chunk_ids, content_hashes=content_hashes)
    generated = Path(str(tmp) + ".npz") if not tmp.exists() and Path(str(tmp) + ".npz").exists() else tmp
    os.replace(generated, target)
    write_json(str(Path(str(target) + ".manifest.json")), manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build local semantic embedding sidecar for v2 SQLite")
    parser.add_argument("db", type=Path); parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5"); parser.add_argument("--model-path")
    parser.add_argument("--batch-size", type=int, default=32); parser.add_argument("--device"); parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    result = build_embedding_index(args.db, args.output, model_name=args.model, model_path=args.model_path, batch_size=args.batch_size, device=args.device)
    if args.manifest: write_json(str(args.manifest), result)
    else: print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
