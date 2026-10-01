"""Scoped lexical retrieval stage with explicit fallback status."""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .contracts import RETRIEVAL_SCHEMA_VERSION, ChunkRecord, RetrievalResult, StageStatus, write_json


_STANDARD_RE = re.compile(r"\b(?:GB|JGJ|CECS|DBJ|CJJ|DL|TB|JG)\s*[-—]?\s*\d{2,6}(?:\s*[-—]\s*\d{4})?\b", re.I)
_CLAUSE_RE = re.compile(r"(?:第\s*)?([0-9]+(?:\.[0-9]+){1,5})(?:\s*条)?")
_ZH_STOPWORDS = {"如何", "怎么", "怎样", "哪些", "什么", "需要", "进行", "可以", "是否", "以及", "关于", "应该", "要求", "情况", "相关", "问题"}
LOGGER = logging.getLogger(__name__)
_SOURCE_SUFFIX_RE = re.compile(r"\.(?:pdf|docx?|txt|md)$", re.I)
_TABLE_TERMS = ("表格", "表中", "见表", "查表", "哪一列", "哪一行", "对应值", "取值表")
_RISK_TERMS = ("风险", "危险", "不得", "禁止", "严禁", "避免", "失效", "事故", "条件", "情形", "当", "如果", "否则", "前提")
_NUMERIC_TERMS = ("限值", "阈值", "范围", "最小", "最大", "允许值", "数值", "多少", "厚度", "宽度", "长度", "强度")
_UNIT_RE = re.compile(r"(?:\d+(?:\.\d+)?\s*)?(?:mm|cm|m|mpa|kpa|pa|kn|n|%|℃|°c|kg|t)\b", re.I)


@dataclass(frozen=True)
class QueryRoute:
    query_type: str
    bm25_weight: float
    vector_weight: float
    lexical_reserve: int
    semantic_reserve: int
    title_weight: float
    hierarchy_policy: str
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_query(query: str, *, top_k: int = 30) -> QueryRoute:
    """Classify query text only; benchmark labels never enter this function."""
    text = str(query or "").strip()
    _, _, standards, clauses = _query_parts(text)
    budget = max(1, int(top_k))
    if standards or clauses:
        return QueryRoute("exact_identifier", 1.8, 0.55, min(budget, 24), min(budget, 6), 0.10, "off", ("structured_identifier",))
    if any(term in text for term in _TABLE_TERMS) or ("表" in text and any(term in text for term in ("数据", "项目", "规定", "要求", "值"))):
        return QueryRoute("table_query", 1.6, 0.85, min(budget, 22), min(budget, 8), 0.15, "off", ("table_language",))
    if _UNIT_RE.search(text) or any(term in text for term in _NUMERIC_TERMS):
        return QueryRoute("numeric_unit", 1.6, 0.85, min(budget, 22), min(budget, 8), 0.10, "off", ("numeric_or_unit_anchor",))
    if any(term in text for term in _RISK_TERMS):
        return QueryRoute("risk_condition", 1.15, 1.65, min(budget, 17), min(budget, 13), 0.10, "auto", ("risk_or_condition_language",))
    if len(text) >= 16 or any(term in text for term in ("为什么", "如何处理", "如何判断", "有哪些", "怎么做")):
        return QueryRoute("natural_semantic", 0.9, 2.35, min(budget, 12), min(budget, 18), 0.10, "off", ("natural_language_paraphrase",))
    lexical = max(1, budget // 2)
    return QueryRoute("general", 1.1, 1.75, lexical, max(1, budget - lexical), 0.10, "auto", ("balanced_default",))


def _readonly_sqlite(path: str | Path) -> sqlite3.Connection:
    target = Path(path).resolve()
    connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


class _UnavailableSemanticRetriever:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def search(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError(self.reason)


def _normalize_standard(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("—", "-").casefold()


def _normalize_source_title(value: str) -> str:
    """Normalize a source filename/title for deterministic query seeding."""
    title = _SOURCE_SUFFIX_RE.sub("", str(value or "")).casefold()
    title = re.sub(r"(?:\(\d+\)|（\d+）)$", "", title).strip()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title)


def _normalize_clause(value: str) -> str:
    match = _CLAUSE_RE.search(value)
    return match.group(1) if match else ""


def _query_parts(query: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return explicit terms, CJK fallback terms, standards and clauses.

    SQLite's default unicode tokenizer is not a Chinese word segmenter.  We
    therefore keep user-provided whitespace terms, split contiguous CJK runs
    into bounded two/three-character windows, and preserve identifiers for
    exact SQL matching.  This is intentionally dependency-free; an optional
    jieba adapter can be added later without changing the retrieval contract.
    """
    explicit: list[str] = []
    fallback: list[str] = []
    standards: list[str] = []
    clauses: list[str] = []
    seen: set[str] = set()
    text = str(query or "")
    for match in _STANDARD_RE.finditer(text):
        value = _normalize_standard(match.group(0))
        if value and value not in standards:
            standards.append(value)
    for match in _CLAUSE_RE.finditer(text):
        value = _normalize_clause(match.group(0))
        if value and value not in clauses:
            clauses.append(value)
    # Keep ASCII/whitespace-delimited terms, but do not reduce a standard
    # identifier to the generic token ``gb``.
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]*|\d+(?:\.\d+)+|[\u4e00-\u9fff]+", text):
        token = raw.casefold().strip()
        if len(token) >= 2 and token not in seen and not token.isdigit():
            explicit.append(token); seen.add(token)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) <= 6 and len(run) >= 2 and run not in seen:
            fallback.append(run.casefold()); seen.add(run.casefold())
        # Two-character windows have good precision for engineering Chinese;
        # three-character windows improve recall for compounds such as 可靠性.
        for width in (2, 3):
            for i in range(max(0, len(run) - width + 1)):
                term = run[i:i + width].casefold()
                if term in _ZH_STOPWORDS or any(term.startswith(stop) or term.endswith(stop) for stop in _ZH_STOPWORDS if len(stop) == len(term)):
                    continue
                if term not in seen:
                    fallback.append(term); seen.add(term)
    # Numeric clauses are meaningful identifiers, not stop words.
    for value in clauses:
        if value not in explicit:
            explicit.append(value)
    return explicit[:24], fallback[:48], standards[:8], clauses[:8]


def _tokens(query: str) -> list[str]:
    """Compatibility view used by diagnostics and the legacy adapter."""
    explicit, fallback, _, _ = _query_parts(query)
    values: list[str] = []
    seen: set[str] = set()
    for token in [*explicit, *fallback]:
        if token and token not in seen:
            values.append(token); seen.add(token)
    return values[:48]


def _fts_query(terms: Iterable[str]) -> str:
    values = []
    for term in terms:
        safe = str(term).replace('"', '""').strip()
        if safe:
            values.append(f'"{safe}"')
    return " OR ".join(values)


def _rrf(rank: int, weight: float, k: int = 60) -> float:
    return float(weight) / float(k + max(1, rank))


def _source_order(chunk: ChunkRecord) -> tuple[Any, ...]:
    pages = chunk.metadata.get("page_numbers") or []
    primary = int(pages[0]) if pages and str(pages[0]).isdigit() else 10**9
    part = int(chunk.metadata.get("part") or 0)
    return (chunk.document_id, primary, str(chunk.location), part, chunk.chunk_id)


def _group_key(chunk: ChunkRecord) -> tuple[str, tuple[str, ...]]:
    heading = chunk.metadata.get("heading_path") or []
    if isinstance(heading, str): heading = [heading]
    return chunk.document_id, tuple(str(x) for x in heading if str(x).strip())


def _row_metadata(row: sqlite3.Row) -> dict[str, Any]:
    try:
        raw = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError, IndexError):
        raw = {}
    metadata = dict(raw) if isinstance(raw, dict) else {}
    metadata.update({
        "retrieval_role": row["retrieval_role"],
        "chunk_type": row["chunk_type"],
        "clause_number": row["clause_number"],
        "standard_number": row["standard_number"],
        "page_numbers": json.loads(row["page_numbers_json"] or "[]"),
        "heading_path": json.loads(row["heading_path_json"] or "[]"),
        "quality_flags": json.loads(row["quality_flags_json"] or "[]"),
        "needs_review": bool(row["needs_review"]),
        "content_hash": row["content_hash"],
        "table_id": row["table_id"],
        "image_id": row["image_id"],
        "table_source": row["table_source"],
    })
    return metadata


def _expand_context(
    db: sqlite3.Connection,
    anchors: list[ChunkRecord],
    *,
    mode: str,
    max_context_chars: int,
    max_expanded_chunks: int,
) -> tuple[list[ChunkRecord], list[dict[str, Any]]]:
    """Expand anchors to same-heading context without changing anchor ranks."""
    groups: dict[tuple[str, tuple[str, ...]], list[ChunkRecord]] = {}
    for anchor in anchors:
        groups.setdefault(_group_key(anchor), []).append(anchor)
    context: list[ChunkRecord] = []
    descriptors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for (document_id, heading), group_anchors in groups.items():
        anchor_ids = [item.chunk_id for item in group_anchors]
        selected: list[ChunkRecord] = []
        for anchor in group_anchors:
            if anchor.chunk_id not in seen:
                anchor.metadata.update({"expanded": False, "anchor": True})
                selected.append(anchor); seen.add(anchor.chunk_id)
            if anchor.parent_id:
                parent = db.execute("SELECT * FROM pipeline_chunks WHERE chunk_id=? AND document_id=? AND retrieval_role='context_only'", (anchor.parent_id, document_id)).fetchone()
                if parent and str(parent["chunk_id"]) not in seen:
                    parent_meta = _row_metadata(parent); parent_meta.update({"score": float("inf"), "context_for": anchor.chunk_id, "expanded": True, "expansion_reason": "parent_hydration", "anchor_chunk_id": anchor.chunk_id})
                    selected.append(ChunkRecord(str(parent["chunk_id"]), document_id, str(parent["location"]), str(parent["text"]), str(parent["parent_id"]), str(parent["source_marker"]), parent_meta)); seen.add(str(parent["chunk_id"]))
        expansion_reason = "same_heading_path" if heading else "parent_only_no_heading"
        if mode in {"subsection", "chapter", "auto"} and heading:
            heading_json = json.dumps(list(heading), ensure_ascii=False)
            rows = db.execute("SELECT *, 0.0 AS score FROM pipeline_chunks WHERE document_id=? AND retrieval_role='retrieval' AND heading_path_json=? ORDER BY primary_page, location, part, chunk_id", (document_id, heading_json)).fetchall()
            for row in rows:
                cid = str(row["chunk_id"])
                if cid in seen: continue
                if len(selected) >= max_expanded_chunks: break
                metadata = _row_metadata(row); metadata.update({"score": float(row["score"] if "score" in row.keys() else 0.0), "expanded": True, "expansion_reason": expansion_reason, "anchor_chunk_id": anchor_ids[0]})
                selected.append(ChunkRecord(cid, document_id, str(row["location"]), str(row["text"]), str(row["parent_id"]), str(row["source_marker"]), metadata)); seen.add(cid)
        selected.sort(key=_source_order)
        if heading and mode != "parent":
            descriptors.append({"document_id": document_id, "heading_path": list(heading), "expansion_level": "subsection", "expansion_reason": expansion_reason, "anchor_chunk_ids": anchor_ids, "chunk_ids": [item.chunk_id for item in selected]})
        context.extend(selected)
    # Anchors are always retained; expansion is bounded after grouping.
    ordered = sorted(context, key=lambda item: (0 if item.metadata.get("anchor") else 1, _source_order(item)))
    bounded: list[ChunkRecord] = []; used = 0
    for item in ordered:
        if item.metadata.get("anchor"):
            bounded.append(item); used += len(item.text); continue
        if len(bounded) >= max_expanded_chunks or used >= max_context_chars: break
        remaining = max_context_chars - used
        if remaining <= 0: break
        if len(item.text) > remaining: item.text = item.text[:remaining]
        if item.text: bounded.append(item); used += len(item.text)
    return bounded, descriptors


def retrieve(
    query: str,
    db_path: str | Path,
    *,
    document_ids: Iterable[str] | None = None,
    top_k: int = 6,
    max_chars: int = 10000,
    allow_scoped_fallback: bool = True,
    semantic_retriever: Any | None = None,
    semantic_top_k: int = 40,
    bm25_weight: float = 1.0,
    vector_weight: float = 2.0,
    fusion_k: int = 60,
    expansion_mode: str = "auto",
    max_expanded_chunks: int = 24,
    # Keep the historical default for direct callers. Production/evaluation
    # callers opt into adaptive routing explicitly so old integrations remain
    # byte-for-byte compatible where they inspect ranking metadata.
    routing_mode: str = "legacy",
    lexical_reserve: int | None = None,
    semantic_reserve: int | None = None,
) -> RetrievalResult:
    if bm25_weight < 0 or vector_weight < 0 or fusion_k <= 0:
        raise ValueError("混合检索权重必须非负且 fusion_k 必须大于 0")
    if routing_mode not in {"adaptive", "legacy"}:
        raise ValueError("routing_mode 必须为 adaptive 或 legacy")
    ids = [str(item) for item in (document_ids or []) if str(item)]
    route = classify_query(query, top_k=max(1, int(top_k)))
    effective_bm25_weight = route.bm25_weight if routing_mode == "adaptive" else float(bm25_weight)
    effective_vector_weight = route.vector_weight if routing_mode == "adaptive" else float(vector_weight)
    effective_lexical_reserve = max(0, int(route.lexical_reserve if lexical_reserve is None else lexical_reserve))
    effective_semantic_reserve = max(0, int(route.semantic_reserve if semantic_reserve is None else semantic_reserve))
    hierarchy_policy = route.hierarchy_policy if expansion_mode == "auto" and routing_mode == "adaptive" else expansion_mode
    tokens = _tokens(query)
    semantic_warning = ""
    semantic_used = False
    lexical_used = False
    with closing(_readonly_sqlite(db_path)) as db:
      with db:
        db.row_factory = sqlite3.Row
        if ids:
            placeholders = ",".join("?" for _ in ids)
            scope_count = db.execute(f"SELECT COUNT(*) FROM pipeline_chunks WHERE document_id IN ({placeholders})", ids).fetchone()[0]
        else:
            scope_count = db.execute("SELECT COUNT(*) FROM pipeline_chunks").fetchone()[0]
        if not scope_count:
            return RetrievalResult(query=query, scope_available=False, retrieval_mode="none", relevance_status="unavailable", scope_document_ids=ids, status=StageStatus(status="ready", stage_version="retrieve.v1"))
        explicit, fallback_terms, standards, clauses = _query_parts(query)
        terms = list(dict.fromkeys([*explicit, *fallback_terms]))
        candidate_k = max(max(1, int(top_k)) * 8, 32)
        candidates: dict[str, dict[str, Any]] = {}
        exact_candidate_ids: list[str] = []

        def add_rows(found: Iterable[sqlite3.Row], channel: str, matched: Iterable[str], weight: float, *, score_key: str = "") -> None:
            for rank, row in enumerate(found, 1):
                cid = str(row["chunk_id"])
                item = candidates.setdefault(cid, {"row": row, "channels": [], "terms": [], "fusion": 0.0, "score": float(row["score"] or 0.0)})
                contribution = _rrf(rank, weight, max(1, int(fusion_k)))
                item["fusion"] += contribution
                if channel != "semantic":
                    item["lexical_fusion"] = float(item.get("lexical_fusion", 0.0)) + contribution
                item.setdefault("channel_contributions", {})[channel] = contribution
                if channel not in item["channels"]: item["channels"].append(channel)
                if score_key:
                    item[f"{score_key}_rank"] = rank
                    item[f"{score_key}_score"] = float(row["score"] or 0.0)
                for term in matched:
                    if term and term not in item["terms"]: item["terms"].append(term)

        where_scope = f" AND c.document_id IN ({','.join('?' for _ in ids)})" if ids else ""
        if terms:
            fts = _fts_query(terms)
            if fts:
                params: list[Any] = [fts, *ids, candidate_k]
                try:
                    found = db.execute(f"SELECT c.*, bm25(pipeline_chunks_fts, 1.0, 1.0, 1.0, 2.0, 5.0, 8.0, 7.0, 3.0, 2.0) AS score FROM pipeline_chunks_fts JOIN pipeline_chunks c USING(chunk_id) WHERE pipeline_chunks_fts MATCH ? AND c.retrieval_role='retrieval'{where_scope} ORDER BY score LIMIT ?", params).fetchall()
                except sqlite3.OperationalError:
                    found = []
                if found:
                    lexical_used = True
                add_rows(found, "bm25", terms, effective_bm25_weight, score_key="bm25")
            like_terms = [term for term in terms if len(term) >= 2][:24]
            if like_terms:
                like = " OR ".join("c.text_search LIKE ?" for _ in like_terms)
                params = [*(f"%{term}%" for term in like_terms), *ids, candidate_k]
                found = db.execute(f"SELECT c.*, 0.0 AS score FROM pipeline_chunks c WHERE c.retrieval_role='retrieval' AND ({like}){where_scope} ORDER BY c.document_id, c.location, c.chunk_id LIMIT ?", params).fetchall()
                if found:
                    lexical_used = True
                add_rows(found, "like", like_terms, 0.35, score_key="like")
        # Exact structured identifiers are high-confidence channels.  The
        # REPLACE expression tolerates OCR/user spacing and hyphen variants.
        for standard in standards:
            params = [standard, *ids, candidate_k]
            found = db.execute(f"SELECT c.*, 0.0 AS score FROM pipeline_chunks c WHERE c.retrieval_role='retrieval' AND lower(replace(replace(c.standard_number, ' ', ''), '—', '-'))=?{where_scope} ORDER BY c.document_id, c.location, c.chunk_id LIMIT ?", params).fetchall()
            if found:
                lexical_used = True
            add_rows(found, "standard_exact", [standard], 8.0, score_key="standard_exact")
            exact_candidate_ids.extend(str(row["chunk_id"]) for row in found)
        for clause in clauses:
            params = [clause, *ids, candidate_k]
            found = db.execute(f"SELECT c.*, 0.0 AS score FROM pipeline_chunks c WHERE c.retrieval_role='retrieval' AND c.clause_number=?{where_scope} ORDER BY c.document_id, c.location, c.chunk_id LIMIT ?", params).fetchall()
            if found:
                lexical_used = True
            add_rows(found, "clause_exact", [clause], 10.0, score_key="clause_exact")
            exact_candidate_ids.extend(str(row["chunk_id"]) for row in found)

        # A document title is provenance metadata, not necessarily repeated in
        # every chunk.  Seed exact/near-exact title queries from the selected
        # scope only, so representative source-name queries remain deterministic
        # without broadening retrieval to unrelated documents.
        title_query = _normalize_source_title(query)
        if len(title_query) >= 4:
            title_scope = f" AND c.document_id IN ({','.join('?' for _ in ids)})" if ids else ""
            try:
                title_rows = db.execute(
                    f"SELECT c.*, 0.0 AS score, d.source_name FROM pipeline_chunks c "
                    f"JOIN pipeline_documents d ON d.document_id=c.document_id "
                    f"WHERE c.retrieval_role='retrieval'{title_scope} "
                    "ORDER BY c.document_id, c.primary_page, c.location, c.part, c.chunk_id",
                    [*ids],
                ).fetchall()
            except sqlite3.OperationalError:
                title_rows = []
            exact_title_rows = []
            contained_title_rows = []
            for row in title_rows:
                source_title = _normalize_source_title(row["source_name"])
                if not source_title:
                    continue
                if title_query == source_title:
                    exact_title_rows.append(row)
                elif title_query in source_title or (len(source_title) >= 4 and source_title in title_query):
                    contained_title_rows.append(row)
            matched_title_rows = exact_title_rows or contained_title_rows
            if matched_title_rows:
                lexical_used = True
                # A title locates a document; it is not proof that every body
                # chunk is relevant.  In explicit scope it contributes no
                # document-wide bonus.  Outside scope retain one weak seed per
                # document for source discovery.
                if not ids:
                    title_seeds: list[sqlite3.Row] = []
                    seen_title_documents: set[str] = set()
                    for row in matched_title_rows:
                        document_id = str(row["document_id"])
                        if document_id not in seen_title_documents:
                            title_seeds.append(row)
                            seen_title_documents.add(document_id)
                    add_rows(title_seeds[:candidate_k], "source_title_exact", [query], route.title_weight if routing_mode == "adaptive" else 12.0, score_key="source_title_exact")

        if semantic_retriever is not None:
            try:
                semantic_rows = semantic_retriever.search(query, db_path, document_ids=ids, top_k=max(1, int(semantic_top_k)))
                for rank, item in enumerate(semantic_rows, 1):
                    cid = str(item.get("chunk_id", ""))
                    if not cid:
                        continue
                    row = db.execute("SELECT *, 0.0 AS score FROM pipeline_chunks WHERE chunk_id=? AND retrieval_role='retrieval'", (cid,)).fetchone()
                    if not row or (ids and str(row["document_id"]) not in ids):
                        continue
                    semantic_used = True
                    current = candidates.setdefault(cid, {"row": row, "channels": [], "terms": [], "fusion": 0.0, "score": 0.0})
                    contribution = _rrf(rank, effective_vector_weight, max(1, int(fusion_k)))
                    current["fusion"] += contribution
                    current["semantic_fusion"] = float(current.get("semantic_fusion", 0.0)) + contribution
                    current.setdefault("channel_contributions", {})["semantic"] = contribution
                    if "semantic" not in current["channels"]: current["channels"].append("semantic")
                    current["semantic_score"] = float(item.get("semantic_score", 0.0))
                    current["semantic_rank"] = int(item.get("semantic_rank", rank))
            except Exception as exc:  # semantic is an optional non-fatal channel
                LOGGER.exception("Semantic retrieval channel failed")
                semantic_warning = f"semantic_unavailable:{type(exc).__name__}"

        ranked = sorted(candidates.values(), key=lambda item: (-float(item["fusion"]), float(item["score"]), str(item["row"]["chunk_id"])))
        if routing_mode == "adaptive":
            by_id = {str(item["row"]["chunk_id"]): item for item in ranked}
            lexical_ranked = sorted(
                (item for item in ranked if float(item.get("lexical_fusion", 0.0)) > 0.0),
                key=lambda item: (-float(item.get("lexical_fusion", 0.0)), float(item["score"]), str(item["row"]["chunk_id"])),
            )
            semantic_ranked = sorted(
                (item for item in ranked if float(item.get("semantic_fusion", 0.0)) > 0.0),
                key=lambda item: (int(item.get("semantic_rank", 10**9)), -float(item.get("semantic_score", 0.0)), str(item["row"]["chunk_id"])),
            )
            reserved_ids: list[str] = []
            for cid in exact_candidate_ids:
                if cid in by_id and cid not in reserved_ids:
                    reserved_ids.append(cid)
            for collection, limit in ((lexical_ranked, effective_lexical_reserve), (semantic_ranked, effective_semantic_reserve)):
                for item in collection[:limit]:
                    cid = str(item["row"]["chunk_id"])
                    if cid not in reserved_ids:
                        reserved_ids.append(cid)
            reserved_set = set(reserved_ids)
            ranked = [*([by_id[cid] for cid in reserved_ids]), *[item for item in ranked if str(item["row"]["chunk_id"]) not in reserved_set]]
        rows = []
        diagnostics: dict[str, dict[str, Any]] = {}
        for item in ranked[:max(1, int(top_k))]:
            row = item["row"]; cid = str(row["chunk_id"]); diagnostics[cid] = item
            rows.append(row)
        mode = "hybrid_lexical" if rows else "lexical"
        relevance = "hit" if rows else "unknown"
        if not rows and allow_scoped_fallback:
            params = ids + [max(1, int(top_k))] if ids else [max(1, int(top_k))]
            where = f"WHERE document_id IN ({','.join('?' for _ in ids)})" if ids else ""
            role_where = "retrieval_role='retrieval' AND "
            rows = db.execute(f"SELECT *, 0.0 AS score FROM pipeline_chunks {where}{' AND ' if where else 'WHERE '}{role_where[:-5]} ORDER BY document_id, location, chunk_id LIMIT ?", params).fetchall()
            mode = "scoped_fallback"; relevance = "unknown"
        chunks: list[ChunkRecord] = []
        used = 0
        for row in rows:
            text = str(row["text"])
            if used + len(text) > max_chars: text = text[:max(0, max_chars - used)]
            if not text: break
            diag = diagnostics.get(str(row["chunk_id"]), {})
            metadata = _row_metadata(row); metadata.update({"score": float(row["score"]), "retrieval_channels": list(diag.get("channels", [])), "matched_terms": list(diag.get("terms", [])), "fusion_score": float(diag.get("fusion", 0.0)), "channel_contributions": dict(diag.get("channel_contributions", {})), "fusion_weights": {"bm25": effective_bm25_weight, "vector": effective_vector_weight, "rrf_k": int(fusion_k)}, "query_route": route.to_dict(), "routing_mode": routing_mode})
            for key in ("bm25_rank", "bm25_score", "like_rank", "like_score", "standard_exact_rank", "clause_exact_rank", "source_title_exact_rank"):
                if key in diag:
                    metadata[key] = diag[key]
            if "semantic_score" in diag:
                metadata["semantic_score"] = float(diag["semantic_score"])
                metadata["semantic_rank"] = int(diag.get("semantic_rank", 0))
            chunks.append(ChunkRecord(str(row["chunk_id"]), str(row["document_id"]), str(row["location"]), text, str(row["parent_id"]), str(row["source_marker"]), metadata))
            used += len(text)
        anchor_chunks = list(chunks)
        # Legacy mode preserves unconditional parent hydration. Adaptive mode
        # hydrates parents only when hierarchy gating enables context.
        seen_parents: set[str] = set()
        hydrated_by_child: dict[str, ChunkRecord] = {}
        hydrate_parents = routing_mode == "legacy" or hierarchy_policy not in {"none", "off", ""}
        for chunk in chunks if hydrate_parents else []:
            if chunk.parent_id and chunk.parent_id not in seen_parents:
                parent = db.execute("SELECT * FROM pipeline_chunks WHERE chunk_id=? AND document_id=? AND retrieval_role='context_only'", (chunk.parent_id, chunk.document_id)).fetchone()
                if parent:
                    parent_meta = _row_metadata(parent); parent_meta.update({"score": float("inf"), "context_for": chunk.chunk_id})
                    hydrated_by_child[chunk.chunk_id] = ChunkRecord(str(parent["chunk_id"]), str(parent["document_id"]), str(parent["location"]), str(parent["text"]), str(parent["parent_id"]), str(parent["source_marker"]), parent_meta)
                    seen_parents.add(chunk.parent_id)
        interleaved: list[ChunkRecord] = []
        for chunk in chunks:
            interleaved.append(chunk)
            parent = hydrated_by_child.get(chunk.chunk_id)
            if parent:
                interleaved.append(parent)
        bounded: list[ChunkRecord] = []
        used = 0
        for chunk in interleaved:
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(chunk.text) > remaining:
                chunk.text = chunk.text[:remaining]
            if chunk.text:
                bounded.append(chunk)
                used += len(chunk.text)
        chunks = bounded
        context_groups: list[dict[str, Any]] = []
        should_expand = bool(anchor_chunks) and hierarchy_policy not in {"none", "off", ""}
        if hierarchy_policy == "auto":
            reliable_heading = any(bool(_group_key(item)[1]) for item in anchor_chunks)
            # A one-item result is considered context-insufficient even when
            # the caller requested top_k=1; this preserves the parent needed
            # to interpret a terse general query while exact/numeric/table
            # routes remain explicitly gated off.
            insufficient_anchors = len(anchor_chunks) < max(2, int(top_k))
            should_expand = reliable_heading and (route.query_type == "risk_condition" or insufficient_anchors)
        if should_expand:
            chunks, context_groups = _expand_context(
                db,
                anchor_chunks,
                mode="subsection" if hierarchy_policy in {"auto", "forced"} else hierarchy_policy,
                max_context_chars=max_chars,
                max_expanded_chunks=max(1, int(max_expanded_chunks)),
            )
        else:
            context_groups = []
        anchor_payload = [
            {"chunk_id": item.chunk_id, "document_id": item.document_id, "location": item.location, "source_marker": item.source_marker, "metadata": dict(item.metadata)}
            for item in anchor_chunks
        ]
    warnings = [semantic_warning] if semantic_warning else []
    result_mode = "hybrid_hierarchical" if context_groups and semantic_used else ("hierarchical" if context_groups else ("hybrid_semantic" if semantic_used and lexical_used and relevance == "hit" else ("semantic" if semantic_used and relevance == "hit" else mode)))
    route_diagnostics = {
        **route.to_dict(),
        "routing_mode": routing_mode,
        "effective_bm25_weight": effective_bm25_weight,
        "effective_vector_weight": effective_vector_weight,
        "lexical_reserve": effective_lexical_reserve,
        "semantic_reserve": effective_semantic_reserve,
        "requested_expansion_mode": expansion_mode,
        "effective_hierarchy_policy": hierarchy_policy,
        "hierarchy_expanded": bool(context_groups),
    }
    result = RetrievalResult(query=query, chunks=chunks, scope_available=True, retrieval_mode=result_mode, relevance_status=relevance, scope_document_ids=ids, status=StageStatus(status="ready", warnings=warnings, stage_version="retrieve.v2"), anchors=anchor_payload, context_groups=context_groups, route_diagnostics=route_diagnostics)
    return result


def retrieve_legacy_knowledge_db(
    query: str,
    db_path: str | Path,
    *,
    document_ids: Iterable[str] | None = None,
    top_k: int = 6,
    max_chars: int = 10000,
    allow_scoped_fallback: bool = True,
) -> RetrievalResult:
    """Retrieve from the existing runtime ``documents/chunks/chunks_fts`` DB.

    This adapter lets the desktop GUI use the extracted retrieval stage before
    the on-disk database is migrated to the new pipeline schema.
    """
    ids = [str(item) for item in (document_ids or []) if str(item)]
    tokens = _tokens(query)
    with closing(_readonly_sqlite(db_path)) as db:
      with db:
        db.row_factory = sqlite3.Row
        where_scope = ""
        scope_params: list[Any] = []
        if ids:
            where_scope = f" AND c.document_id IN ({','.join('?' for _ in ids)})"
            scope_params.extend(ids)
        count = db.execute(f"SELECT COUNT(*) FROM chunks c WHERE 1=1{where_scope}", scope_params).fetchone()[0]
        if not count:
            return RetrievalResult(query=query, scope_available=False, retrieval_mode="none", relevance_status="unavailable", scope_document_ids=ids, status=StageStatus(status="ready", stage_version="retrieve.v1"))
        rows: list[sqlite3.Row] = []
        if tokens:
            fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34)*2)}"' for token in tokens)
            params: list[Any] = [fts_query, *scope_params, max(1, int(top_k))]
            try:
                rows = db.execute(f"SELECT c.*, bm25(chunks_fts) AS score FROM chunks_fts JOIN chunks c USING(chunk_id) WHERE chunks_fts MATCH ?{where_scope} ORDER BY score LIMIT ?", params).fetchall()
            except sqlite3.OperationalError:
                rows = []
        mode = "lexical"; relevance = "hit" if rows else "unknown"
        if not rows and allow_scoped_fallback:
            params = [*scope_params, max(1, int(top_k))]
            rows = db.execute(f"SELECT c.*, 0.0 AS score FROM chunks c WHERE 1=1{where_scope} ORDER BY c.document_id, c.location, c.chunk_id LIMIT ?", params).fetchall()
            mode = "scoped_fallback"
        chunks: list[ChunkRecord] = []
        used = 0
        for row in rows:
            text = str(row["text"])
            if used + len(text) > max_chars:
                text = text[: max(0, max_chars - used)]
            if not text:
                break
            chunks.append(ChunkRecord(str(row["chunk_id"]), str(row["document_id"]), str(row["location"]), text, metadata={"score": float(row["score"])}))
            used += len(text)
    for chunk in chunks:
        chunk.source_marker = f"[KB:{chunk.document_id}:{chunk.location}]"
    return RetrievalResult(query=query, chunks=chunks, scope_available=True, retrieval_mode=mode, relevance_status=relevance, scope_document_ids=ids, status=StageStatus(status="ready", stage_version="retrieve.v1"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieve scoped chunks from pipeline SQLite")
    parser.add_argument("query"); parser.add_argument("db", type=Path); parser.add_argument("output", type=Path)
    parser.add_argument("--document-id", action="append", default=[]); parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--semantic-index", type=Path); parser.add_argument("--semantic-model-path")
    parser.add_argument("--bm25-weight", type=float, default=1.0); parser.add_argument("--vector-weight", type=float, default=2.0); parser.add_argument("--fusion-k", type=int, default=60)
    args = parser.parse_args(argv)
    semantic = None
    if args.semantic_index:
        try:
            from .semantic_retrieve import SemanticRetriever
            semantic = SemanticRetriever(args.semantic_index, model_path=args.semantic_model_path)
        except Exception as exc:
            # Keep the CLI usable with the deterministic channels when the
            # optional model package/weights are not installed.
            LOGGER.exception("Semantic retriever CLI initialization failed")
            stable_reason = f"semantic_unavailable:{type(exc).__name__}"
            print(json.dumps({"warning": stable_reason}, ensure_ascii=False))
            semantic = _UnavailableSemanticRetriever(stable_reason)
    result = retrieve(args.query, args.db, document_ids=args.document_id, top_k=args.top_k, semantic_retriever=semantic, bm25_weight=args.bm25_weight, vector_weight=args.vector_weight, fusion_k=args.fusion_k)
    write_json(str(args.output), result.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
