"""Materialize the project owner's completed manual review and publish Gold."""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT_ROOT / "benchmarks" / "rag" / "formal_v1" / "candidates.jsonl"
DEFAULT_REVIEW = PROJECT_ROOT / "benchmarks" / "rag" / "formal_v1" / "review_attested.jsonl"
DEFAULT_DB = PROJECT_ROOT / "knowledge_pipeline" / "test" / "results" / "index" / "pipeline.sqlite3"
DEFAULT_GOLD = PROJECT_ROOT / "benchmarks" / "rag" / "formal_v1" / "gold_v1"
REVIEWER = "project-owner-user-attestation"


def _benchmark_module():
    path = PROJECT_ROOT / "scripts" / "formal_rag_benchmark.py"
    spec = importlib.util.spec_from_file_location("formal_rag_benchmark", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load benchmark module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def attest(candidates: Path, review_path: Path, reviewed_at: str) -> list[dict]:
    records = [json.loads(line) for line in candidates.read_text(encoding="utf-8").splitlines() if line.strip()]
    for record in records:
        direct_ids = list(record.get("candidate_relevant_chunk_ids") or [])
        record["gold_answer"] = str(record.get("candidate_answer") or "")
        record["review"] = {
            "original_pdf_opened": True,
            "page_verified": True,
            "text_verified": True,
            "answer_verified": True,
            "no_answer_verified": True if record.get("expected_no_answer") else None,
            "reviewer_id": REVIEWER,
            "reviewed_at": reviewed_at,
            "manual_relevant_chunk_ids": direct_ids,
            "relevance_labels": [
                {"chunk_id": chunk_id, "label": "direct"} for chunk_id in direct_ids
            ],
            "manual_notes": (
                "用户于当前对话明确声明已对全部正式候选逐项人工核对且无误；"
                "本记录据此固化为项目负责人单人审核，不代表双人独立标注。"
            ),
        }
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with review_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--reviewed-at", default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args()
    records = attest(args.candidates, args.review, args.reviewed_at)
    benchmark = _benchmark_module()
    manifest = benchmark.publish(args.review, args.db, args.gold)
    manifest["attestation"] = {
        "reviewer_id": REVIEWER,
        "review_scope": "all_150_records",
        "statement": "用户明确声明已完成全部候选的人工核对且无误",
        "review_model": "single_project_owner_attestation",
        "double_annotation": False,
    }
    benchmark._write_json(args.gold / "manifest.json", manifest)
    print(json.dumps({"review_records": len(records), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
