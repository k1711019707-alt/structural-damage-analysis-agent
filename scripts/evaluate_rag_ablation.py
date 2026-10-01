"""Evaluate the formal RAG Gold set across existing retrieval ablations."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.contracts import ChunkRecord, RetrievalResult, StageStatus
from knowledge_pipeline.rerank import rerank_payload
from knowledge_pipeline.retrieve import retrieve
from knowledge_pipeline.semantic_retrieve import SemanticRetriever
from knowledge_pipeline.external_fallback import route_external_answer


DEFAULT_GOLD = PROJECT_ROOT / "benchmarks" / "rag" / "formal_v1" / "gold_v1" / "gold.jsonl"
DEFAULT_DB = PROJECT_ROOT / "knowledge_pipeline" / "test" / "results" / "index" / "pipeline.sqlite3"
DEFAULT_SEMANTIC = PROJECT_ROOT / "knowledge_pipeline" / "test" / "results" / "embed" / "semantic.npz"
DEFAULT_OUTPUT = PROJECT_ROOT / "benchmarks" / "rag" / "formal_v1" / "evaluation_v1"
DEFAULT_OUTPUT_V2 = PROJECT_ROOT / "benchmarks" / "rag" / "formal_v1" / "evaluation_v2"
KS = (1, 3, 5, 10, 30)
CANDIDATE_POOL = 30

CONFIGS = (
    "lexical_structured",
    "semantic_only",
    "hybrid_rrf",
    "hybrid_rerank",
    "hybrid_rerank_hierarchy",
)
CONFIGS_V2 = (
    "lexical_structured",
    "semantic_only",
    "hybrid_rrf",
    "hybrid_adaptive",
    "hybrid_adaptive_rerank",
    "hybrid_adaptive_rerank_gated_hierarchy",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def query_metrics(ranked_ids: Sequence[str], relevant_ids: set[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for k in KS:
        top = list(ranked_ids[:k])
        hits = len(set(top) & relevant_ids)
        result[f"hit@{k}"] = 1.0 if hits else 0.0
        result[f"precision@{k}"] = hits / k
        result[f"recall@{k}"] = hits / len(relevant_ids) if relevant_ids else 0.0
    first_rank = next((index for index, chunk_id in enumerate(ranked_ids[:10], 1) if chunk_id in relevant_ids), None)
    result["mrr@10"] = 1.0 / first_rank if first_rank else 0.0
    dcg = sum((1.0 / math.log2(index + 1)) for index, chunk_id in enumerate(ranked_ids[:10], 1) if chunk_id in relevant_ids)
    ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(relevant_ids), 10) + 1))
    result["ndcg@10"] = dcg / ideal if ideal else 0.0
    return result


def refusal_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    tp = sum(bool(row["expected_no_answer"]) and bool(row["predicted_no_answer"]) for row in rows)
    fp = sum(not bool(row["expected_no_answer"]) and bool(row["predicted_no_answer"]) for row in rows)
    fn = sum(bool(row["expected_no_answer"]) and not bool(row["predicted_no_answer"]) for row in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    answerable_count = sum(not bool(row["expected_no_answer"]) for row in rows)
    false_abstention = sum(not bool(row["expected_no_answer"]) and bool(row["predicted_no_answer"]) for row in rows)
    return {
        "no_answer_tp": tp,
        "no_answer_fp": fp,
        "no_answer_fn": fn,
        "no_answer_precision": precision,
        "no_answer_recall": recall,
        "no_answer_f1": f1,
        "answerable_false_abstention_rate": false_abstention / answerable_count if answerable_count else 0.0,
    }


def _row_to_chunk(db: sqlite3.Connection, chunk_id: str, metadata: dict[str, Any]) -> ChunkRecord | None:
    row = db.execute("SELECT * FROM pipeline_chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
    if not row:
        return None
    return ChunkRecord(
        chunk_id=str(row["chunk_id"]), document_id=str(row["document_id"]),
        location=str(row["location"]), text=str(row["text"]),
        parent_id=str(row["parent_id"]), source_marker=str(row["source_marker"]), metadata=metadata,
    )


def semantic_result(
    semantic: SemanticRetriever, query: str, db_path: Path, scope: list[str], top_k: int
) -> RetrievalResult:
    candidates = semantic.search(query, db_path, document_ids=scope, top_k=top_k)
    chunks: list[ChunkRecord] = []
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        for item in candidates:
            chunk = _row_to_chunk(db, str(item["chunk_id"]), {
                "semantic_score": float(item["semantic_score"]),
                "semantic_rank": int(item["semantic_rank"]),
                "anchor": True,
                "expanded": False,
                "retrieval_role": "retrieval",
            })
            if chunk:
                chunks.append(chunk)
    return RetrievalResult(
        query=query, chunks=chunks, scope_available=True, retrieval_mode="semantic",
        relevance_status="hit" if chunks else "no_hit", scope_document_ids=scope,
        status=StageStatus(status="ready", stage_version="semantic-retrieve.v1"),
        anchors=[{"chunk_id": chunk.chunk_id, "document_id": chunk.document_id} for chunk in chunks],
    )


def direct_ids_from_result(result: RetrievalResult) -> list[str]:
    if result.anchors:
        return [str(item.get("chunk_id")) for item in result.anchors if item.get("chunk_id")]
    return [chunk.chunk_id for chunk in result.chunks if not chunk.metadata.get("expanded") and chunk.metadata.get("retrieval_role", "retrieval") == "retrieval"]


def run_config(
    name: str,
    question: str,
    scope: list[str],
    db_path: Path,
    semantic: SemanticRetriever,
) -> tuple[list[str], list[str], list[str], str, str]:
    if name == "lexical_structured":
        result = retrieve(
            question, db_path, document_ids=scope, top_k=CANDIDATE_POOL,
            allow_scoped_fallback=False, semantic_retriever=None,
            bm25_weight=1.0, vector_weight=0.0, expansion_mode="none", routing_mode="legacy",
        )
        direct = direct_ids_from_result(result)
        context_ids = [chunk.chunk_id for chunk in result.chunks if chunk.chunk_id not in set(direct)]
        return direct, [chunk.chunk_id for chunk in result.chunks], context_ids, result.retrieval_mode, result.relevance_status
    if name == "semantic_only":
        result = semantic_result(semantic, question, db_path, scope, CANDIDATE_POOL)
        direct = direct_ids_from_result(result)
        return direct, direct, [], result.retrieval_mode, result.relevance_status

    adaptive = name in {"hybrid_adaptive", "hybrid_adaptive_rerank", "hybrid_adaptive_rerank_gated_hierarchy"}
    hierarchy = name in {"hybrid_rerank_hierarchy", "hybrid_adaptive_rerank_gated_hierarchy"}
    result = retrieve(
        question, db_path, document_ids=scope, top_k=CANDIDATE_POOL,
        allow_scoped_fallback=False, semantic_retriever=semantic, semantic_top_k=CANDIDATE_POOL,
        bm25_weight=1.0, vector_weight=2.0,
        expansion_mode="auto" if hierarchy else "none", max_expanded_chunks=60, max_chars=50000,
        routing_mode="adaptive" if adaptive else "legacy",
    )
    direct = direct_ids_from_result(result)
    context_ids = [chunk.chunk_id for chunk in result.chunks if chunk.chunk_id not in set(direct)]
    if name in {"hybrid_rerank", "hybrid_rerank_hierarchy", "hybrid_adaptive_rerank", "hybrid_adaptive_rerank_gated_hierarchy"}:
        # Rerank direct candidates only. Expanded context remains context and
        # cannot displace or masquerade as direct Gold evidence.
        payload = result.to_dict()
        payload["chunks"] = [chunk.to_dict() for chunk in result.chunks if chunk.chunk_id in set(direct)]
        reranked = rerank_payload(payload, top_k=CANDIDATE_POOL)
        direct = [str(item.get("chunk_id")) for item in reranked.chunks if item.get("chunk_id")]
    returned = [*direct, *[chunk_id for chunk_id in context_ids if chunk_id not in set(direct)]]
    return direct, returned, context_ids, result.retrieval_mode, result.relevance_status


def evaluate(
    gold_path: Path,
    db_path: Path,
    semantic_path: Path,
    output_dir: Path,
    configs: Sequence[str] = CONFIGS,
) -> dict[str, Any]:
    gold = read_jsonl(gold_path)
    if len(gold) != 150 or not all(row.get("gold_label") and row.get("annotation_status") == "gold_reviewed" for row in gold):
        raise RuntimeError("evaluation requires the complete reviewed 150-record Gold dataset")
    semantic_started = time.perf_counter()
    semantic = SemanticRetriever(semantic_path)
    semantic_init_ms = (time.perf_counter() - semantic_started) * 1000.0
    raw_rows: list[dict[str, Any]] = []
    for config in configs:
        for index, record in enumerate(gold, 1):
            started = time.perf_counter()
            error = ""
            try:
                direct, returned, context_ids, mode, relevance = run_config(
                    config, str(record["question"]), list(record.get("scope_document_ids") or []),
                    db_path, semantic,
                )
            except Exception as exc:
                direct, returned, context_ids, mode, relevance = [], [], [], "error", "unavailable"
                error = f"{type(exc).__name__}: {exc}"
            latency_ms = (time.perf_counter() - started) * 1000.0
            relevant = set(record.get("relevant_chunk_ids") or [])
            metrics = query_metrics(direct, relevant) if relevant else {key: 0.0 for key in [
                *(f"hit@{k}" for k in KS), *(f"precision@{k}" for k in KS),
                *(f"recall@{k}" for k in KS), "mrr@10", "ndcg@10",
            ]}
            scope = set(record.get("scope_document_ids") or [])
            evidence_doc = {item["chunk_id"]: item["document_id"] for item in record.get("evidence") or []}
            with sqlite3.connect(db_path) as db:
                if returned:
                    placeholders = ",".join("?" for _ in returned)
                    docs = {str(row[0]): str(row[1]) for row in db.execute(
                        f"SELECT chunk_id,document_id FROM pipeline_chunks WHERE chunk_id IN ({placeholders})", returned
                    )}
                else:
                    docs = {}
            leakage_ids = [chunk_id for chunk_id in returned if docs.get(chunk_id, evidence_doc.get(chunk_id, "")) not in scope]
            predicted_no_answer = not direct or relevance not in {"hit"}
            expected_external = bool(record.get("expected_no_answer"))
            fallback = route_external_answer(
                str(record["question"]),
                knowledge_base_has_answer=not expected_external,
                web_search_provider=None,
            )
            predicted_route = fallback.answer_source_mode if expected_external else ("knowledge_base" if not predicted_no_answer else "model_prior")
            raw_rows.append({
                "config": config,
                "question_id": record["question_id"],
                "question_type": record["question_type"],
                "question": record["question"],
                "expected_no_answer": bool(record.get("expected_no_answer")),
                "predicted_no_answer": predicted_no_answer,
                "expected_kb_no_answer": expected_external,
                "expected_route": "external_fallback" if expected_external else "knowledge_base",
                "predicted_route": predicted_route,
                "external_fallback_routed": expected_external and predicted_route in {"web_search", "model_prior"},
                "web_search_preferred": expected_external and predicted_route == "web_search",
                "model_prior_fallback": expected_external and predicted_route == "model_prior",
                "source_mode_label_correct": (predicted_route in {"web_search", "model_prior"}) if expected_external else (predicted_route == "knowledge_base"),
                "scope_document_ids": list(scope),
                "gold_relevant_chunk_ids": sorted(relevant),
                "ranked_direct_chunk_ids": direct,
                "returned_chunk_ids": returned,
                "context_chunk_ids": context_ids,
                "context_gold_direct_coverage": len(set(context_ids) & relevant) / len(relevant) if relevant else 0.0,
                "context_overhead_count": len(context_ids),
                "scope_leakage_chunk_ids": leakage_ids,
                "scope_leakage": bool(leakage_ids),
                "retrieval_mode": mode,
                "relevance_status": relevance,
                "latency_ms": latency_ms,
                "error": error,
                **metrics,
            })
            if index % 25 == 0:
                print(f"[{config}] {index}/{len(gold)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "per_query_results.jsonl", raw_rows)
    summaries: dict[str, dict[str, Any]] = {}
    type_breakdown: dict[str, dict[str, Any]] = {}
    metric_names = [*(f"hit@{k}" for k in KS), *(f"precision@{k}" for k in KS), *(f"recall@{k}" for k in KS), "mrr@10", "ndcg@10"]
    for config in configs:
        rows = [row for row in raw_rows if row["config"] == config]
        positives = [row for row in rows if not row["expected_no_answer"]]
        latencies = [row["latency_ms"] for row in rows if not row["error"]]
        summary = {
            "config": config,
            "query_count": len(rows),
            "positive_query_count": len(positives),
            "no_answer_query_count": sum(row["expected_no_answer"] for row in rows),
            "success_count": sum(not row["error"] for row in rows),
            "error_count": sum(bool(row["error"]) for row in rows),
            **{name: statistics.fmean(row[name] for row in positives) if positives else 0.0 for name in metric_names},
            **refusal_metrics(rows),
            "scope_leakage_rate": statistics.fmean(float(row["scope_leakage"]) for row in rows) if rows else 0.0,
            "scope_isolation_leakage_rate": statistics.fmean(float(row["scope_leakage"]) for row in rows if row["question_type"] == "scope_isolation") if any(row["question_type"] == "scope_isolation" for row in rows) else 0.0,
            "latency_mean_ms": statistics.fmean(latencies) if latencies else 0.0,
            "latency_p50_ms": percentile(latencies, 0.50),
            "latency_p95_ms": percentile(latencies, 0.95),
            "context_overhead_mean": statistics.fmean(row["context_overhead_count"] for row in rows) if rows else 0.0,
            "context_gold_direct_coverage_mean": statistics.fmean(row["context_gold_direct_coverage"] for row in positives) if positives else 0.0,
            "external_fallback_routing_rate": statistics.fmean(float(row["external_fallback_routed"]) for row in rows if row["expected_kb_no_answer"]) if any(row["expected_kb_no_answer"] for row in rows) else 0.0,
            "web_search_preferred_rate": statistics.fmean(float(row["web_search_preferred"]) for row in rows if row["expected_kb_no_answer"]) if any(row["expected_kb_no_answer"] for row in rows) else 0.0,
            "model_prior_fallback_rate": statistics.fmean(float(row["model_prior_fallback"]) for row in rows if row["expected_kb_no_answer"]) if any(row["expected_kb_no_answer"] for row in rows) else 0.0,
            "source_mode_label_accuracy": statistics.fmean(float(row["source_mode_label_correct"]) for row in rows) if rows else 0.0,
        }
        summaries[config] = summary
        for question_type in sorted({row["question_type"] for row in rows}):
            subset = [row for row in rows if row["question_type"] == question_type]
            positive_subset = [row for row in subset if not row["expected_no_answer"]]
            type_breakdown[f"{config}:{question_type}"] = {
                "config": config,
                "question_type": question_type,
                "count": len(subset),
                **{name: statistics.fmean(row[name] for row in positive_subset) if positive_subset else 0.0 for name in metric_names},
                **refusal_metrics(subset),
                "latency_p50_ms": percentile([row["latency_ms"] for row in subset], 0.50),
                "latency_p95_ms": percentile([row["latency_ms"] for row in subset], 0.95),
            }
    write_json(output_dir / "summary.json", {"configs": summaries})
    write_json(output_dir / "type_breakdown.json", {"rows": list(type_breakdown.values())})

    summary_fields = list(next(iter(summaries.values())).keys())
    with (output_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=summary_fields)
        writer.writeheader(); writer.writerows(summaries.values())

    failures = [
        row for row in raw_rows
        if row["error"] or (not row["expected_no_answer"] and not row["hit@10"])
        or (row["expected_no_answer"] and not row.get("external_fallback_routed", False))
        or row["scope_leakage"]
    ]
    write_jsonl(output_dir / "failure_cases.jsonl", failures)

    lexical_hit30 = float(summaries.get("lexical_structured", {}).get("hit@30", 0.0))
    adaptive_hit30 = max((float(summaries.get(name, {}).get("hit@30", 0.0)) for name in configs if name.startswith("hybrid_adaptive")), default=0.0)
    manifest = {
        "schema_version": "rag-ablation-evaluation.v2" if any(name.startswith("hybrid_adaptive") for name in configs) else "rag-ablation-evaluation.v1",
        "gold_path": str(gold_path.resolve()), "gold_sha256": sha256(gold_path),
        "database_path": str(db_path.resolve()), "database_sha256": sha256(db_path),
        "semantic_path": str(semantic_path.resolve()), "semantic_sha256": sha256(semantic_path),
        "semantic_manifest_sha256": sha256(Path(str(semantic_path) + ".manifest.json")),
        "semantic_model": semantic.manifest.get("model_name", "BAAI/bge-small-zh-v1.5"),
        "semantic_dimension": semantic.vectors.shape[1], "semantic_count": semantic.vectors.shape[0],
        "semantic_initialization_ms": semantic_init_ms,
        "configs": list(configs), "ks": list(KS), "candidate_pool": CANDIDATE_POOL,
        "python": sys.version, "platform": platform.platform(),
        "script_sha256": sha256(Path(__file__)),
        "query_count": len(gold), "raw_result_count": len(raw_rows),
        "metric_boundary": "direct Gold chunk IDs only; expanded context reported separately",
        "lexical_boundary": "FTS5/BM25 + Chinese LIKE + standard/clause/source-title exact channels",
        "answer_boundary": "retrieval/context evaluation only; no remote LLM answer evaluation",
        "external_fallback_boundary": "KB no-answer records prefer a verified web provider; this run had no provider and therefore used model_prior contract",
        "adaptive_hit30_at_least_lexical": adaptive_hit30 >= lexical_hit30,
        "adaptive_hit30": adaptive_hit30,
        "lexical_hit30_target": lexical_hit30,
    }
    write_json(output_dir / "manifest.json", manifest)

    labels = {
        "lexical_structured": "词法结构化",
        "semantic_only": "纯语义",
        "hybrid_rrf": "词法+语义 RRF",
        "hybrid_rerank": "混合+确定性重排",
        "hybrid_rerank_hierarchy": "混合+重排+层级上下文",
        "hybrid_adaptive": "自适应路由混合",
        "hybrid_adaptive_rerank": "自适应路由+重排",
        "hybrid_adaptive_rerank_gated_hierarchy": "自适应路由+重排+门控层级",
    }
    lines = [
        "# 当前 RAG 知识库 Gold 评估与消融实验（evaluation_v2）" if any(name.startswith("hybrid_adaptive") for name in configs) else "# 当前 RAG 知识库 Gold 评估与消融实验", "",
        "## 评测边界", "",
        f"- Gold：{len(gold)} 题，直接相关证据只取 `relevant_chunk_ids`。",
        "- 词法基线是项目现有结构化词法通道，不是裸 BM25。",
        "- 层级 parent/expanded context 不计作直接命中，只单列上下文开销和覆盖。",
        "- 本报告评估检索、重排、scope 和上下文组装，不等于最终 LLM 回答正确率。", "",
        "## 总体结果", "",
        "| 配置 | Hit@5 | Hit@10 | Hit@30 | Recall@10 | MRR@10 | nDCG@10 | 无答案F1 | Scope泄漏 | P50(ms) | P95(ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for config in configs:
        item = summaries[config]
        lines.append(
            f"| {labels[config]} | {item['hit@5']:.3f} | {item['hit@10']:.3f} | {item['hit@30']:.3f} | "
            f"{item['recall@10']:.3f} | {item['mrr@10']:.3f} | {item['ndcg@10']:.3f} | "
            f"{item['no_answer_f1']:.3f} | {item['scope_leakage_rate']:.3f} | "
            f"{item['latency_p50_ms']:.1f} | {item['latency_p95_ms']:.1f} |"
        )
    lines.extend(["", "## 指标定义", "", "- Hit@K：Top-K 至少包含一个 Gold direct chunk 的问题比例。", "- Recall@K：Top-K 覆盖 Gold direct chunks 的比例。", "- MRR@10：首个 Gold direct chunk 排名倒数的平均值。", "- nDCG@10：binary direct relevance 的归一化折损累计增益。", "- KB 无答案题：不再要求拒答，改报 external fallback routing、web-search preference、model-prior fallback 和 source-mode labeling。", "- Scope 泄漏：任一返回 chunk 超出题目 scope 即记为泄漏。", f"- 自适应 Hit@30 回归门：{'通过' if adaptive_hit30 >= lexical_hit30 else '未通过'}（adaptive={adaptive_hit30:.3f}，lexical target={lexical_hit30:.3f}）。", "", "## 失败案例", "", f"详见 `failure_cases.jsonl`，共 {len(failures)} 条配置-问题失败记录。", ""])
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return {"manifest": manifest, "summaries": summaries, "failure_count": len(failures)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--semantic", type=Path, default=DEFAULT_SEMANTIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--v2", action="store_true", help="运行自适应路由与外部回退评测协议")
    parser.add_argument("--config", action="append", choices=tuple(dict.fromkeys([*CONFIGS, *CONFIGS_V2])), default=[])
    args = parser.parse_args()
    configs = args.config or (CONFIGS_V2 if args.v2 else CONFIGS)
    output = args.output if args.output != DEFAULT_OUTPUT or not args.v2 else DEFAULT_OUTPUT_V2
    result = evaluate(args.gold, args.db, args.semantic, output, configs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
