"""Run an end-to-end RAGAS evaluation for the PDF candidate set.

This evaluator deliberately uses the candidate-matching test SQLite rather
than the currently activated production manifest when their document scopes
differ.  Candidate answers are used as references only when explicitly
allowed; they remain separate from the formal Gold Set.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
os.environ.setdefault("RAGAS_DEBUG_TRACKING", "false")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_CANDIDATES = PROJECT_ROOT / "benchmarks" / "rag" / "ragas_pdf_v1" / "candidates.jsonl"
DEFAULT_DB = PROJECT_ROOT / "knowledge_pipeline" / "test" / "results" / "index" / "pipeline.sqlite3"
_local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
DEFAULT_GUI_CONFIG = (Path(_local_app_data) if _local_app_data else Path.home()) / "YOLO11DamageDesktop" / "config" / "gui_settings.json"
DEFAULT_CONFIG = DEFAULT_GUI_CONFIG if DEFAULT_GUI_CONFIG.is_file() else PROJECT_ROOT / "gui_api_config.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "benchmarks" / "rag" / "ragas_pdf_evaluation_v1"


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


def load_runtime_config(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    # The running GUI stores the API values under the unified settings
    # envelope (api.responses_url/key/model); the legacy project file stores
    # them at the top level. Accept both so evaluation uses the same endpoint
    # that the GUI currently displays.
    api_payload = payload.get("api") if isinstance(payload.get("api"), dict) else payload
    # One-run overrides allow a caller to test a rotated endpoint/key without
    # writing credentials into project configuration or evaluation artifacts.
    key = str(os.environ.get("RAGAS_RESPONSES_KEY") or api_payload.get("responses_key") or "").strip()
    if not key:
        raise RuntimeError("GUI Responses API key 未配置")
    base_url = str(os.environ.get("RAGAS_RESPONSES_URL") or api_payload.get("responses_url") or "").strip().rstrip("/")
    base_url = re.sub(r"/v1/(?:responses|chat/completions)$", "/v1", base_url, flags=re.I)
    if not re.search(r"/v1$", base_url, flags=re.I):
        base_url += "/v1"
    return {"base_url": base_url, "api_key": key, "model": str(os.environ.get("RAGAS_RESPONSES_MODEL") or api_payload.get("responses_model") or "gpt-5.6-sol")}


def validate_candidates(rows: list[dict[str, Any]], *, require_reviewed: bool = False) -> None:
    if not rows:
        raise RuntimeError("候选集为空")
    for index, row in enumerate(rows, 1):
        required = ("question", "candidate_answer", "reference_contexts", "candidate_relevant_documents")
        missing = [field for field in required if not row.get(field)]
        if missing:
            raise RuntimeError(f"第 {index} 条候选缺少字段: {', '.join(missing)}")
        if require_reviewed and (row.get("annotation_status") != "gold_reviewed" or row.get("gold_label") is not True):
            raise RuntimeError("候选集仍包含非 gold_reviewed 记录；如确认人工核对已完成，请先更新审核字段或不使用 --require-reviewed")


def build_llm(runtime: dict[str, str], timeout: int):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=runtime["model"], api_key=runtime["api_key"], base_url=runtime["base_url"],
        temperature=0.0, timeout=timeout, max_retries=1, use_responses_api=False, streaming=True,
    )


def generate_answer(llm: Any, question: str, contexts: list[str]) -> str:
    context_text = "\n\n--- 检索片段 ---\n\n".join(contexts)
    prompt = (
        "你是土木工程知识库问答评测器。只能依据检索片段回答问题；如果片段不足以回答，明确说信息不足。"
        "保留原文中的数值、单位、公式、否定词和适用条件，不得引用片段之外的知识。"
        f"\n\n问题：{question}\n\n检索片段：\n{context_text}"
    )
    # Streaming applies to answer generation only. RAGAS metrics still receive
    # the fully assembled response after the stream closes.
    parts: list[str] = []
    for chunk in llm.stream(prompt):
        content = getattr(chunk, "content", chunk)
        if isinstance(content, list):
            content = "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
        if content:
            parts.append(str(content))
    return "".join(parts).strip()


def retrieve_row(question: str, document_ids: list[str], db_path: Path, top_k: int, max_chars: int) -> tuple[list[str], list[str], str]:
    from knowledge_pipeline.retrieve import retrieve

    result = retrieve(
        question, db_path, document_ids=document_ids, top_k=top_k, max_chars=max_chars,
        allow_scoped_fallback=False, semantic_retriever=None,
        bm25_weight=1.0, vector_weight=0.0, expansion_mode="auto", routing_mode="adaptive",
    )
    contexts = [str(chunk.text) for chunk in result.chunks if str(chunk.text).strip()]
    ids = [str(chunk.chunk_id) for chunk in result.chunks]
    return ids, contexts, str(result.retrieval_mode)


def run_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    candidates = read_jsonl(args.candidates.resolve())
    validate_candidates(candidates, require_reviewed=args.require_reviewed)
    if args.limit:
        candidates = candidates[:args.limit]
    runtime = load_runtime_config(args.config.resolve())
    llm = build_llm(runtime, args.timeout)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    generated: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    def generate_one(index: int, candidate: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        question = str(candidate["question"])
        scope = [str(item) for item in candidate.get("candidate_relevant_documents") or []]
        retrieved_ids, contexts, retrieval_mode = retrieve_row(question, scope, args.db.resolve(), args.top_k, args.max_chars)
        response = generate_answer(llm, question, contexts)
        return {
            "_index": index,
            "question_id": candidate.get("question_id", f"ragas-{index:04d}"),
            "user_input": question,
            "response": response,
            "reference": str(candidate.get("candidate_answer") or ""),
            "reference_contexts": [str(item) for item in candidate.get("reference_contexts") or []],
            "retrieved_contexts": contexts,
            "reference_context_ids": list(candidate.get("candidate_relevant_chunk_ids") or []),
            "retrieved_context_ids": retrieved_ids,
            "scope_document_ids": scope,
            "source_candidate_status": candidate.get("annotation_status"),
            "source_gold_label": candidate.get("gold_label"),
            "retrieval_mode": retrieval_mode,
            "latency_ms": (time.perf_counter() - started) * 1000.0,
        }

    existing_generation = output / "generation_rows.jsonl"
    if args.reuse_generation and existing_generation.is_file():
        generated = read_jsonl(existing_generation)
        existing_failures = output / "failures.jsonl"
        if existing_failures.is_file():
            failures = read_jsonl(existing_failures)
    else:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ragas-answer") as pool:
            futures = {pool.submit(generate_one, index, candidate): (index, candidate) for index, candidate in enumerate(candidates, 1)}
            for future in as_completed(futures):
                index, candidate = futures[future]
                question_id = candidate.get("question_id", f"ragas-{index:04d}")
                try:
                    generated.append(future.result())
                except Exception as exc:
                    failures.append({"question_id": question_id, "error": f"{type(exc).__name__}: {exc}"})
        generated.sort(key=lambda row: int(row.pop("_index", 0)))
    write_jsonl(output / "generation_rows.jsonl", generated)
    write_jsonl(output / "failures.jsonl", failures)

    metric_rows: list[dict[str, Any]] = []
    metric_error = ""
    if generated:
        try:
            from ragas import evaluate
            from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
            from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness
            from ragas.run_config import RunConfig

            samples = [SingleTurnSample(
                user_input=row["user_input"], response=row["response"],
                retrieved_contexts=row["retrieved_contexts"], reference=row["reference"],
                reference_contexts=row["reference_contexts"],
                retrieved_context_ids=row["retrieved_context_ids"], reference_context_ids=row["reference_context_ids"],
            ) for row in generated]
            dataset = EvaluationDataset(samples=samples)
            from scripts.generate_ragas_pdf_testset import ChineseHashEmbeddings
            result = evaluate(
                dataset, metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()],
                llm=llm, embeddings=ChineseHashEmbeddings(),
                run_config=RunConfig(timeout=args.timeout, max_retries=1, max_wait=5, max_workers=args.metric_workers, seed=42),
                raise_exceptions=False, show_progress=True, batch_size=args.metric_batch_size,
            )
            dataframe = result.to_pandas()
            for index, row in enumerate(dataframe.to_dict(orient="records")):
                metric_rows.append({"question_id": generated[index]["question_id"], **{key: (float(value) if isinstance(value, (int, float)) else value) for key, value in row.items()}})
        except Exception as exc:
            metric_error = f"{type(exc).__name__}: {exc}"
    write_jsonl(output / "metric_rows.jsonl", metric_rows)
    aggregates: dict[str, Any] = {"scored_count": len(metric_rows), "metric_error": metric_error}
    if metric_rows:
        names = [key for key, value in metric_rows[0].items() if key not in {"question_id", "user_input", "response", "reference", "retrieved_contexts", "reference_contexts"} and isinstance(value, (int, float))]
        aggregates["mean"] = {}
        aggregates["non_finite_count"] = {}
        for name in names:
            values = [float(row[name]) for row in metric_rows if isinstance(row.get(name), (int, float)) and row[name] == row[name]]
            aggregates["mean"][name] = statistics.mean(values) if values else None
            aggregates["non_finite_count"][name] = sum(1 for row in metric_rows if isinstance(row.get(name), (int, float)) and row[name] != row[name])
    manifest = {
        "schema_version": "ragas-pdf-rag-evaluation.v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "ragas_version": "0.4.3", "python_executable": sys.executable,
        "candidate_path": str(args.candidates.resolve()), "candidate_sha256": sha256(args.candidates.resolve()),
        "database_path": str(args.db.resolve()), "database_sha256": sha256(args.db.resolve()),
        "input_count": len(candidates), "generated_count": len(generated), "failure_count": len(failures),
        "metric_count": len(metric_rows), "model": runtime["model"], "endpoint_category": "openai_compatible_remote",
        "api_key_present": True, "api_key_value_recorded": False,
        "reference_boundary": "candidate_answer used as reference; input remains non-Gold unless separately promoted",
        "metrics": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
        "top_k": args.top_k, "max_chars": args.max_chars, "aggregates": aggregates,
    }
    write_json(output / "manifest.json", manifest)
    report = ["# RAGAS PDF RAG 评估", "", f"- 输入记录：{len(candidates)}；已生成回答：{len(generated)}；失败：{len(failures)}；已评分：{len(metric_rows)}", "- 评估知识库：候选集对应的测试 SQLite，按 candidate_relevant_documents 限定 scope。", "- reference 使用候选集 candidate_answer；这不是正式 Gold Set。", "", "## 聚合指标", ""]
    report.append(json.dumps(aggregates, ensure_ascii=False, indent=2))
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0, help="仅评估前 N 条；0 表示全部")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument("--reuse-generation", action="store_true", help="复用 output/generation_rows.jsonl，跳过回答生成")
    # Keep metric judging conservative by default: upstream providers may rate-limit
    # concurrent RAGAS requests, and a smaller batch also makes failures easier to
    # isolate when evaluating a single answer at a time.
    parser.add_argument("--metric-batch-size", type=int, default=2)
    parser.add_argument("--metric-workers", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(run_evaluation(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
