"""Generate a review-only RAGAS testset from already parsed test PDFs.

Run this script with ``D:\\anaconda\\envs\\RAGAS\\python.exe``.  It reads the
project SQLite database in read-only mode so scanned PDFs are not OCRed twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

# This run is intentionally local/orchestrated by the project.  RAGAS 0.4.x
# otherwise attempts to send anonymous telemetry, which is unnecessary for a
# reproducible benchmark build and can add a long network timeout.
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
os.environ.setdefault("RAGAS_DEBUG_TRACKING", "false")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "knowledge_pipeline" / "test" / "测试文档"
DEFAULT_DB = PROJECT_ROOT / "knowledge_pipeline" / "test" / "results" / "index" / "pipeline.sqlite3"
DEFAULT_CONFIG = PROJECT_ROOT / "gui_api_config.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "benchmarks" / "rag" / "ragas_pdf_v1"
# Keep the original 3:2:1 document balance while expanding the candidate pool
# from 60 to 150 records: 75 standards + 50 paper + 25 article questions.
DEFAULT_QUOTAS = {"规范": 75, "水下": 50, "信息化": 25}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", text)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def readonly_sqlite(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


@dataclass
class SourceDocument:
    document_id: str
    source_name: str
    source_path: Path
    source_sha256: str
    chunks: list[dict[str, Any]]


@dataclass
class DocumentUnit:
    unit_id: str
    document_id: str
    source_name: str
    source_path: str
    source_sha256: str
    text: str
    chunk_ids: list[str]
    pages: list[int]
    source_markers: list[str]
    headings: list[str]

    def metadata(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "document_id": self.document_id,
            "source_name": self.source_name,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "chunk_ids": list(self.chunk_ids),
            "page_numbers": list(self.pages),
            "source_markers": list(self.source_markers),
            "heading_paths": list(self.headings),
        }


def discover_sources(source_dir: Path, db_path: Path) -> list[SourceDocument]:
    pdfs = {path.resolve(): path for path in source_dir.glob("*.pdf") if path.is_file()}
    if not pdfs:
        raise RuntimeError(f"测试文档目录中没有 PDF: {source_dir}")
    discovered: list[SourceDocument] = []
    with readonly_sqlite(db_path) as db:
        documents = db.execute(
            "SELECT document_id,source_name,source_path,source_sha256 FROM pipeline_documents"
        ).fetchall()
        for row in documents:
            raw_path = Path(str(row["source_path"]))
            try:
                resolved = raw_path.resolve()
            except OSError:
                continue
            if resolved not in pdfs:
                continue
            chunk_rows = db.execute(
                """SELECT chunk_id,text,source_marker,page_numbers_json,heading_path_json,
                          chunk_type,clause_number,standard_number,table_id,needs_review,
                          quality_flags_json,primary_page,location,part
                   FROM pipeline_chunks
                   WHERE document_id=? AND retrieval_role='retrieval'
                   ORDER BY primary_page,location,part,chunk_id""",
                (str(row["document_id"]),),
            ).fetchall()
            chunks: list[dict[str, Any]] = []
            for chunk in chunk_rows:
                chunks.append({
                    "chunk_id": str(chunk["chunk_id"]),
                    "text": str(chunk["text"] or "").strip(),
                    "source_marker": str(chunk["source_marker"] or ""),
                    "page_numbers": json.loads(chunk["page_numbers_json"] or "[]"),
                    "heading_path": json.loads(chunk["heading_path_json"] or "[]"),
                    "chunk_type": str(chunk["chunk_type"] or "paragraph"),
                    "clause_number": str(chunk["clause_number"] or ""),
                    "standard_number": str(chunk["standard_number"] or ""),
                    "table_id": str(chunk["table_id"] or ""),
                    "needs_review": bool(chunk["needs_review"]),
                    "quality_flags": json.loads(chunk["quality_flags_json"] or "[]"),
                })
            discovered.append(SourceDocument(
                document_id=str(row["document_id"]),
                source_name=str(row["source_name"]),
                source_path=pdfs[resolved],
                source_sha256=str(row["source_sha256"] or sha256(pdfs[resolved])),
                chunks=chunks,
            ))
    missing = sorted(path.name for path in pdfs if path not in {item.source_path.resolve() for item in discovered})
    if missing:
        raise RuntimeError("以下测试 PDF 没有匹配的已索引文档: " + ", ".join(missing))
    return sorted(discovered, key=lambda item: item.source_name)


def build_units(source: SourceDocument, *, target_units: int, max_chars: int = 50000) -> list[DocumentUnit]:
    chunks = [item for item in source.chunks if item["text"]]
    if not chunks:
        return []
    total_chars = sum(len(item["text"]) for item in chunks)
    target_chars = min(max_chars, max(1200, math.ceil(total_chars / max(1, target_units))))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    used = 0
    for chunk in chunks:
        text_len = len(chunk["text"])
        if current and used + text_len > target_chars:
            groups.append(current)
            current = []
            used = 0
        current.append(chunk)
        used += text_len
    if current:
        groups.append(current)
    units: list[DocumentUnit] = []
    for index, group in enumerate(groups, 1):
        pages = sorted({int(page) for item in group for page in item["page_numbers"] if str(page).isdigit()})
        markers = list(dict.fromkeys(item["source_marker"] for item in group if item["source_marker"]))
        headings = list(dict.fromkeys(" > ".join(map(str, item["heading_path"])) for item in group if item["heading_path"]))
        header = f"来源文档：{source.source_name}\n页码：{','.join(map(str, pages)) or '未知'}\n"
        body = "\n\n".join(item["text"] for item in group)
        units.append(DocumentUnit(
            unit_id=f"{source.document_id}:ragas-unit:{index:04d}",
            document_id=source.document_id,
            source_name=source.source_name,
            source_path=str(source.source_path.resolve()),
            source_sha256=source.source_sha256,
            text=header + body,
            chunk_ids=[item["chunk_id"] for item in group],
            pages=pages,
            source_markers=markers,
            headings=headings,
        ))
    return units


def extract_terms(unit: DocumentUnit) -> list[str]:
    """Extract compact, deterministic themes for RAGAS single-hop scenarios.

    The default RAGAS document transforms discover themes with extra LLM calls.
    These terms are deliberately conservative: headings, identifiers, units,
    and medium-length Chinese technical phrases already present in the parsed
    chunk.  They are only used to select a context; the question and answer
    are still generated and constrained by RAGAS' prompt.
    """
    text = unit.text
    candidates: list[str] = []
    candidates.extend(unit.headings)
    # Source markers are provenance only; never offer them as query terms.
    candidates.extend(re.findall(r"(?:GB/T|GB|JGJ|DB\s*/?T)?\s*\d{2,5}(?:[-—/]\d{2,5})+", text, flags=re.I))
    candidates.extend(re.findall(r"\d+(?:\.\d+)?\s*(?:mm|cm|m|kN|MPa|Pa|%|℃|°C|d|年|天|级)", text, flags=re.I))
    # Keep phrases long enough to be meaningful, but avoid making each whole
    # OCR line a theme.  De-duplication is stable for reproducible output.
    candidates.extend(re.findall(r"[\u4e00-\u9fff]{3,12}", text))
    terms: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        value = re.sub(r"\s+", " ", str(value)).strip(" ,，。；;:：()（）[]【】")
        if not value or len(value) < 2:
            continue
        key = normalize_text(value)
        if key in seen:
            continue
        seen.add(key)
        terms.append(value)
        if len(terms) >= 24:
            break
    if not terms:
        terms = ["工程检测", "结构工程", "技术要求"]
    return terms


def generation_context(unit: DocumentUnit) -> str:
    """Return source text for RAGAS without synthetic provenance headers.

    Page/chunk markers are retained in the candidate evidence, but exposing
    them to the question synthesizer can lead to questions about ``KB:...``
    identifiers instead of the engineering content itself.
    """
    lines: list[str] = []
    for line in unit.text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("来源文档：", "页码：", "文档：", "来源：", "章节路径：", "条款：")):
            continue
        if re.fullmatch(r"\[KB:[^\]]+\]", stripped):
            continue
        lines.append(line)
    return "\n".join(lines).strip() or unit.text


class ChineseHashEmbeddings:
    """Deterministic character n-gram embeddings for RAGAS graph transforms."""

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = int(dimensions)

    def _embed(self, text: str) -> list[float]:
        normalized = normalize_text(text)
        grams: list[str] = []
        for width in (2, 3):
            grams.extend(normalized[index:index + width] for index in range(max(0, len(normalized) - width + 1)))
        if not grams and normalized:
            grams = [normalized]
        vector = [0.0] * self.dimensions
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            raw = int.from_bytes(digest, "little")
            vector[raw % self.dimensions] += 1.0 if raw & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


def load_runtime_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    key = str(payload.get("responses_key") or "").strip()
    if not key:
        raise RuntimeError("GUI Responses API key 未配置")
    base_url = str(payload.get("responses_url") or "").strip().rstrip("/")
    base_url = re.sub(r"/v1/(?:responses|chat/completions)$", "/v1", base_url, flags=re.I)
    if not re.search(r"/v1$", base_url, flags=re.I):
        base_url += "/v1"
    return {
        "base_url": base_url,
        "api_key": key,
        "model": str(payload.get("responses_model") or "gpt-5.6-sol").strip(),
    }


def quota_for(source_name: str, *, smoke: bool = False) -> int:
    if smoke:
        return 1
    for marker, quota in DEFAULT_QUOTAS.items():
        if marker in source_name:
            return quota
    return 5


def bind_context(context: str, units: Sequence[DocumentUnit]) -> list[DocumentUnit]:
    needle = normalize_text(context)
    if not needle:
        return []
    exact = [unit for unit in units if needle in normalize_text(unit.text) or normalize_text(unit.text) in needle]
    if exact:
        return exact[:2]
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_.%-]+", needle))
    scored: list[tuple[float, DocumentUnit]] = []
    for unit in units:
        unit_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_.%-]+", normalize_text(unit.text)))
        score = len(tokens & unit_tokens) / max(1, len(tokens))
        scored.append((score, unit))
    best = sorted(scored, key=lambda item: (-item[0], item[1].unit_id))
    return [unit for score, unit in best[:1] if score > 0.0]


def project_candidate(index: int, raw: dict[str, Any], units: Sequence[DocumentUnit]) -> dict[str, Any]:
    contexts = [str(item) for item in (raw.get("reference_contexts") or []) if str(item).strip()]
    bound_units: list[DocumentUnit] = []
    for context in contexts:
        for unit in bind_context(context, units):
            if unit.unit_id not in {item.unit_id for item in bound_units}:
                bound_units.append(unit)
    evidence = [{
        "candidate_relevance": "direct",
        "unit_id": unit.unit_id,
        "document_id": unit.document_id,
        "source_name": unit.source_name,
        "source_path": unit.source_path,
        "source_sha256": unit.source_sha256,
        "chunk_ids": list(unit.chunk_ids),
        "page_numbers": list(unit.pages),
        "source_markers": list(unit.source_markers),
        "heading_paths": list(unit.headings),
        "evidence_text": next((context for context in contexts if unit in bind_context(context, units)), unit.text),
    } for unit in bound_units]
    synthesizer = str(raw.get("synthesizer_name") or "unknown")
    question_type = "multi_hop" if "multi_hop" in synthesizer else "single_hop"
    return {
        "schema_version": "ragas-pdf-candidate.v1",
        "question_id": f"ragas-{index:04d}",
        "question": str(raw.get("user_input") or "").strip(),
        "question_type": question_type,
        "difficulty": "hard" if question_type == "multi_hop" else "medium",
        "candidate_answer": str(raw.get("reference") or "").strip(),
        "reference_contexts": contexts,
        "synthesizer_name": synthesizer,
        "candidate_relevant_documents": list(dict.fromkeys(unit.document_id for unit in bound_units)),
        "candidate_relevant_chunk_ids": list(dict.fromkeys(chunk_id for unit in bound_units for chunk_id in unit.chunk_ids)),
        "evidence": evidence,
        "expected_no_answer": False,
        "answerable": True,
        "annotation_status": "needs_human_review",
        "gold_label": False,
        "gold_answer": "",
        "generation_method": "ragas-0.4.3",
        "review": {
            "reviewer_id": "",
            "reviewed_at": "",
            "original_pdf_opened": False,
            "page_verified": False,
            "text_verified": False,
            "answer_verified": False,
            "manual_relevant_chunk_ids": [],
            "relevance_labels": [],
            "manual_notes": "",
        },
    }


def run_generation(
    source: SourceDocument,
    units: list[DocumentUnit],
    quota: int,
    runtime: dict[str, Any],
    *,
    timeout: int,
) -> list[dict[str, Any]]:
    from langchain_core.documents import Document
    from langchain_openai import ChatOpenAI
    from ragas.run_config import RunConfig
    from ragas.testset import TestsetGenerator
    from ragas.testset.graph import KnowledgeGraph, Node, NodeType
    from ragas.testset.persona import Persona
    from ragas.testset.synthesizers.base import QueryLength, QueryStyle
    # The specific synthesizer inherits its sample implementation from
    # single_hop.base, whose isinstance check requires this exact class (the
    # similarly named specific.SingleHopScenario is a different type in 0.4.3).
    from ragas.testset.synthesizers.single_hop.base import SingleHopScenario
    from ragas.testset.synthesizers import SingleHopSpecificQuerySynthesizer
    from ragas.executor import Executor

    documents = [Document(page_content=unit.text, metadata=unit.metadata()) for unit in units]
    llm = ChatOpenAI(
        model=runtime["model"],
        api_key=runtime["api_key"],
        base_url=runtime["base_url"],
        temperature=0.1,
        timeout=timeout,
        max_retries=1,
        use_responses_api=False,
    )
    # Construct only the graph data needed by the single-hop synthesizer.  In
    # particular, do not call generate_with_langchain_docs(), whose default
    # transforms perform multiple LLM passes (headlines, summaries, themes,
    # NER, similarity, ... ) before query generation.
    nodes = [
        Node(
            type=NodeType.CHUNK,
            properties={
                "page_content": generation_context(unit),
                "document_metadata": unit.metadata(),
                "entities": extract_terms(unit),
            },
        )
        for unit in units
    ]
    knowledge_graph = KnowledgeGraph(nodes=nodes, relationships=[])
    generator = TestsetGenerator.from_langchain(
        llm=llm,
        embedding_model=ChineseHashEmbeddings(),
        knowledge_graph=knowledge_graph,
        llm_context=(
            "所有问题和参考答案必须使用简体中文，面向土木工程、结构工程或工程检测人员。"
            "问题必须能够仅依据给定上下文回答，保留数值、单位、否定词和适用条件；"
            "不得询问文件名本身，不得引入上下文之外的规范或工程结论。"
        ),
    )
    generator.persona_list = [Persona(
        name="土木工程检测工程师",
        role_description="关注混凝土结构设计、工程检测、病害识别和材料检测，要求依据原文给出可核查的技术回答。",
    )]
    synthesizer = SingleHopSpecificQuerySynthesizer(
        llm=generator.llm,
        llm_context=(
            "只使用给定上下文生成简体中文问题和参考答案。问题应询问一个明确的工程事实、"
            "条款要求、检测方法或适用条件；答案必须保留原文中的数值、单位和限制条件。"
        ),
    )
    # RAGAS 0.4.3 has a bug in its scenario Executor when one remote request
    # fails: with raise_exceptions=False it returns a float sentinel and later
    # crashes while iterating it (``TypeError: 'float' object is not
    # iterable``).  We already have deterministic nodes and terms, so create
    # the single-hop scenarios locally and use RAGAS only for the actual
    # query/reference generation.  This preserves the RAGAS prompt and output
    # schema while making retries and partial failures observable here.
    persona = generator.persona_list[0]
    scenarios = []
    for index in range(quota):
        node = nodes[index % len(nodes)]
        terms = list(node.properties.get("entities") or ["工程检测"])
        scenarios.append(SingleHopScenario(
            nodes=[node],
            style=QueryStyle.PERFECT_GRAMMAR,
            length=QueryLength.MEDIUM,
            persona=persona,
            term=terms[index % len(terms)],
        ))
    run_config = RunConfig(timeout=timeout, max_retries=1, max_wait=5, max_workers=2, seed=42)
    rows: list[dict[str, Any]] = []
    # Keep network failures bounded to a small batch.  RAGAS' Executor only
    # returns after the entire submitted set completes, so submitting 75
    # scenarios at once can lose all in-memory progress when a request hangs.
    for offset in range(0, len(scenarios), 10):
        executor = Executor(
            desc="Generating Samples",
            raise_exceptions=False,
            run_config=run_config,
            keep_progress_bar=True,
            batch_size=2,
        )
        for scenario in scenarios[offset:offset + 10]:
            executor.submit(synthesizer.generate_sample, scenario=scenario)
        try:
            samples = executor.results()
        except Exception:
            samples = []
        for sample in samples:
            if sample is None or isinstance(sample, (float, int)):
                continue
            if hasattr(sample, "model_dump"):
                row = sample.model_dump()
            elif hasattr(sample, "dict"):
                row = sample.dict()
            else:
                row = dict(sample)
            row["synthesizer_name"] = synthesizer.name
            rows.append(row)
    return rows


def generate(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = args.source_dir.resolve()
    db_path = args.db.resolve()
    output_dir = args.output.resolve()
    runtime = load_runtime_config(args.config.resolve())
    sources = discover_sources(source_dir, db_path)
    all_raw: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    all_units: list[DocumentUnit] = []
    failures: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        quota = quota_for(source.source_name, smoke=args.smoke)
        # RAGAS default transforms make several LLM calls per input document.
        # Keep a small, fixed number of page-contiguous source units; the
        # requested sample quota controls question count, not transform cost.
        unit_target = 4 if len(source.chunks) < 50 else 6
        units = build_units(source, target_units=unit_target)
        all_units.extend(units)
        started_at = datetime.now(timezone.utc).isoformat()
        error = ""
        raw_rows: list[dict[str, Any]] = []
        resumed_rows: list[dict[str, Any]] = []
        partial_path = output_dir / "partial" / f"{source.document_id}.raw.jsonl"
        if args.resume and partial_path.is_file():
            try:
                raw_rows = [json.loads(line) for line in partial_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except (OSError, json.JSONDecodeError) as exc:
                failures.append({"document_id": source.document_id, "source_name": source.source_name, "error": f"resume_read_{type(exc).__name__}: {exc}"})
                raw_rows = []
            if len(raw_rows) >= quota:
                raw_rows = raw_rows[:quota]
            else:
                resumed_rows = list(raw_rows)
                raw_rows = []
        try:
            if not raw_rows:
                missing_quota = quota - len(resumed_rows)
                raw_rows = run_generation(source, units, missing_quota, runtime, timeout=args.timeout)
                raw_rows = resumed_rows + raw_rows
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failures.append({"document_id": source.document_id, "source_name": source.source_name, "error": error})
        for row in raw_rows:
            row["_source_document_id"] = source.document_id
            row["_source_name"] = source.source_name
        all_raw.extend(raw_rows)
        coverage.append({
            "document_id": source.document_id,
            "source_name": source.source_name,
            "source_sha256": source.source_sha256,
            "retrieval_chunk_count": len(source.chunks),
            "ragas_unit_count": len(units),
            "target_sample_count": quota,
            "actual_sample_count": len(raw_rows),
            "started_at": started_at,
            "error": error,
        })
        write_jsonl(output_dir / "partial" / f"{source.document_id}.raw.jsonl", raw_rows)
    for index, raw in enumerate(all_raw, 1):
        relevant_units = [unit for unit in all_units if unit.document_id == str(raw.get("_source_document_id") or "")]
        all_candidates.append(project_candidate(index, raw, relevant_units))
    write_jsonl(output_dir / "ragas_raw.jsonl", all_raw)
    write_jsonl(output_dir / "candidates.jsonl", all_candidates)
    write_jsonl(output_dir / "review_template.jsonl", all_candidates)
    write_jsonl(output_dir / "failures.jsonl", failures)
    duplicate_questions = [question for question, count in Counter(normalize_text(row["question"]) for row in all_candidates).items() if question and count > 1]
    manifest = {
        "schema_version": "ragas-pdf-testset-run.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ragas_version": "0.4.3",
        "python_executable": sys.executable,
        "source_directory": str(source_dir),
        "database_path": str(db_path),
        "database_sha256": sha256(db_path),
        "script_sha256": sha256(Path(__file__)),
        "model": runtime["model"],
        "endpoint_category": "openai_compatible_remote",
        "api_key_present": True,
        "api_key_value_recorded": False,
        "embedding": {"name": "deterministic_chinese_char_ngram_hash", "dimensions": 512, "production_semantic_model": False},
        "smoke": bool(args.smoke),
        "target_sample_count": sum(item["target_sample_count"] for item in coverage),
        "actual_sample_count": len(all_candidates),
        "source_count": len(sources),
        "failure_count": len(failures),
        "duplicate_question_count": len(duplicate_questions),
        "annotation_boundary": "all records are needs_human_review and gold_label=false",
        "rerun_command": f'"{sys.executable}" "{Path(__file__).resolve()}" --output "{output_dir}"' + (" --smoke" if args.smoke else ""),
        "coverage": coverage,
    }
    write_json(output_dir / "manifest.json", manifest)
    lines = [
        "# RAGAS PDF 候选测试集", "",
        f"- 来源目录：`{source_dir}`",
        f"- RAGAS：0.4.3；解释器：`{sys.executable}`",
        f"- 目标/实际：{manifest['target_sample_count']} / {manifest['actual_sample_count']}",
        f"- 失败文档：{manifest['failure_count']}；重复问题：{manifest['duplicate_question_count']}",
        "- 所有记录均为 `needs_human_review` / `gold_label: false`，不得直接作为 Gold。", "",
        "## 文档覆盖", "",
        "| 文档 | retrieval chunks | RAGAS units | 目标 | 实际 | 错误 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in coverage:
        lines.append(f"| {item['source_name']} | {item['retrieval_chunk_count']} | {item['ragas_unit_count']} | {item['target_sample_count']} | {item['actual_sample_count']} | {item['error']} |")
    lines.extend(["", "## 审核要求", "", "逐题打开原 PDF 对照页码，核对 OCR、表格行列、数值单位、否定词、适用条件、问题和参考答案；人工确认后方可另行发布 Gold。", ""])
    (output_dir / "COVERAGE_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--smoke", action="store_true", help="每份 PDF 只生成 1 条候选")
    parser.add_argument("--resume", action="store_true", help="复用 output/partial 中已完成且数量达标的文档结果")
    args = parser.parse_args()
    print(json.dumps(generate(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
