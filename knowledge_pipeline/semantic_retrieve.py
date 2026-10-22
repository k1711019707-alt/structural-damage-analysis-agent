"""Qdrant-only semantic retrieval over the local knowledge base."""
from __future__ import annotations

import argparse
import os
import json
from contextlib import closing
import sys
from pathlib import Path
from typing import Any

from .contracts import SemanticRetrievalResult, StageStatus, write_json
from .qdrant_store import QdrantSemanticRetriever, readonly_sqlite as _readonly_sqlite

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


class SemanticRetriever(QdrantSemanticRetriever):
    def __init__(
        self,
        index_path: str | Path,
        *,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        model_path: str | None = None,
        embedder: Any | None = None,
        expected_index_sha256: str = "",
        expected_manifest_sha256: str = "",
        client: Any | None = None,
    ):
        super().__init__(
            index_path, model_name=model_name, model_path=model_path,
            embedder=embedder, expected_index_sha256=expected_index_sha256,
            expected_manifest_sha256=expected_manifest_sha256, client=client,
        )


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
    parser = argparse.ArgumentParser(description="Search the local Qdrant semantic collection")
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
