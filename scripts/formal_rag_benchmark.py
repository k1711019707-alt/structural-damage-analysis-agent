"""Build and review a traceable 150-question RAG benchmark.

The automatic output is a formal *candidate* benchmark package, not a human
Gold set.  Gold publication is deliberately gated on page-level PDF review.
The input SQLite database and all source PDFs are opened read-only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "knowledge_pipeline" / "test" / "results" / "index" / "pipeline.sqlite3"
DEFAULT_OUTPUT = PROJECT_ROOT / "benchmarks" / "rag" / "formal_v1"
SCHEMA_VERSION = "formal-rag-benchmark.v1"
DEFAULT_SEED = 20260918

QUOTAS = {
    "exact_identifier": 25,
    "natural_paraphrase": 35,
    "numeric_unit": 25,
    "table_query": 20,
    "multi_evidence": 15,
    "scope_isolation": 10,
    "no_answer": 10,
    "risk_negation_exception": 10,
}

TYPE_PREFIX = {
    "exact_identifier": "exact",
    "natural_paraphrase": "paraphrase",
    "numeric_unit": "numeric",
    "table_query": "table",
    "multi_evidence": "multi",
    "scope_isolation": "scope",
    "no_answer": "no-answer",
    "risk_negation_exception": "risk",
}

UNIT_RE = re.compile(
    r"(?<![\d.])(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千]+)\s*"
    r"(?:%|mm²?|cm²?|m²?|MPa|kPa|Pa|N|kN|kg|t|d|h|min|s|℃|°|年|月|天|小时|"
    r"分钟|秒|倍|级|层|根|处|组|次)(?![A-Za-z])",
    re.IGNORECASE,
)
RISK_RE = re.compile(r"不得|不应|严禁|必须|应当|除外|禁止|不宜|不可|不能|仅当|否则|至少|至多")
CLAUSE_RE = re.compile(r"^[1-9]\d*(?:\.\d+){1,4}$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；;])\s+|\n+")


class BenchmarkError(RuntimeError):
    """Raised when benchmark generation or publication violates its contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise BenchmarkError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def _safe_json(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _display_content(row: dict[str, Any], limit: int = 900) -> str:
    text = str(row.get("text") or "")
    if "内容：" in text:
        text = text.split("内容：", 1)[1]
    text = re.sub(r"\n?来源：\[KB:.*$", "", text, flags=re.DOTALL)
    text = re.sub(r"^(?:表头：)?", "", text.strip())
    text = _normalize_space(text.replace("｜", " | "))
    if not text:
        text = _normalize_space(str(row.get("text_search") or row.get("text_raw") or ""))
    return text[:limit].rstrip()


def _topic(row: dict[str, Any]) -> str:
    def clean(value: str) -> str:
        value = _normalize_space(value.replace("|", " ").replace("｜", " "))
        value = re.sub(r"^[（(]?\d+(?:\.\d+)*[)）.、]?\s*", "", value)
        value = value.strip(" ：:，,；;。.-—_")
        chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
        symbol = len(re.findall(r"[^\w\u4e00-\u9fff\s]", value))
        if not 4 <= chinese or len(value) > 52 or symbol > max(3, len(value) // 8):
            return ""
        return value

    headings = _safe_json(row.get("heading_path_json"), [])
    for heading in reversed(headings if isinstance(headings, list) else []):
        value = clean(str(heading))
        if value:
            return value
    content = _display_content(row, 180)
    for candidate in re.split(r"[。！？；;]", content):
        value = clean(candidate[:80])
        if value:
            return value
    return "该证据片段"


def _has_readable_topic(row: dict[str, Any]) -> bool:
    return _topic(row) != "该证据片段"


def _table_topic(row: dict[str, Any]) -> str:
    headings = _safe_json(row.get("heading_path_json"), [])
    if isinstance(headings, list):
        for heading in reversed(headings):
            value = _normalize_space(str(heading))
            if 3 <= len(value) <= 50 and len(re.findall(r"[\u4e00-\u9fff]", value)) >= 3:
                return value
    content = _display_content(row, 240)
    cells = [cell.strip() for cell in content.split("|") if cell.strip()]
    headers = [cell for cell in cells[:6] if len(cell) <= 24]
    if headers:
        return "、".join(headers[:4])
    return f"第{row.get('primary_page')}页表格"


def _answer_excerpt(rows: Sequence[dict[str, Any]], limit: int = 900) -> str:
    parts: list[str] = []
    for row in rows:
        content = _display_content(row, limit)
        if content and content not in parts:
            parts.append(content)
    answer = "；".join(parts)
    return answer[:limit].rstrip("；")


def _required_facts(rows: Sequence[dict[str, Any]]) -> list[str]:
    text = " ".join(_display_content(row, 1000) for row in rows)
    facts: list[str] = []
    for match in UNIT_RE.findall(text):
        value = _normalize_space(match)
        if value and value not in facts:
            facts.append(value)
        if len(facts) >= 8:
            break
    if not facts:
        for sentence in SENTENCE_SPLIT_RE.split(text):
            sentence = _normalize_space(sentence)
            if 8 <= len(sentence) <= 160:
                facts.append(sentence)
            if len(facts) >= 2:
                break
    return facts


def _stable_rows(rows: Iterable[dict[str, Any]], seed: int, salt: str) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> str:
        identity = str(row.get("chunk_id") or row.get("document_id") or row)
        return hashlib.sha256(f"{seed}:{salt}:{identity}".encode("utf-8")).hexdigest()

    return sorted(rows, key=key)


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    return db


def _load_snapshot(db_path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not db_path.is_file():
        raise BenchmarkError(f"benchmark SQLite does not exist: {db_path}")
    with _connect_read_only(db_path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"pipeline_documents", "pipeline_chunks", "pipeline_chunk_pages", "pipeline_tables"}
        missing = sorted(required - tables)
        if missing:
            raise BenchmarkError(f"incompatible benchmark SQLite; missing tables: {missing}")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BenchmarkError(f"SQLite integrity_check failed: {integrity}")
        documents = {
            str(row["document_id"]): dict(row)
            for row in db.execute("SELECT * FROM pipeline_documents ORDER BY document_id")
        }
        chunks = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM pipeline_chunks WHERE retrieval_role='retrieval' "
                "ORDER BY document_id, COALESCE(primary_page, 0), chunk_id"
            )
        ]
    if not chunks:
        raise BenchmarkError("benchmark SQLite contains no retrieval chunks")
    return chunks, documents


def _resolve_sources(
    documents: dict[str, dict[str, Any]], source_roots: Sequence[Path]
) -> dict[str, dict[str, str]]:
    wanted = {str(doc.get("source_sha256") or "") for doc in documents.values()} - {""}
    resolved: dict[str, dict[str, str]] = {}
    seen: set[Path] = set()
    for root in source_roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() and root.suffix.lower() == ".pdf" else root.rglob("*.pdf")
        for path in candidates:
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            digest = _sha256(path)
            if digest in wanted and digest not in resolved:
                resolved[digest] = {"source_path": str(path), "source_name": path.name, "source_sha256": digest}
            if wanted and wanted.issubset(resolved):
                return resolved
    return resolved


def _evidence(row: dict[str, Any], doc: dict[str, Any], resolved: dict[str, dict[str, str]]) -> dict[str, Any]:
    source_sha = str(doc.get("source_sha256") or "")
    source = resolved.get(source_sha, {})
    pages = _safe_json(row.get("page_numbers_json"), [])
    if not pages and row.get("primary_page") is not None:
        pages = [int(row["primary_page"])]
    return {
        "chunk_id": row["chunk_id"],
        "document_id": row["document_id"],
        "source_name": source.get("source_name") or doc.get("source_name") or "",
        "source_path": source.get("source_path") or doc.get("source_path") or "",
        "source_sha256": source_sha,
        "page_numbers": pages,
        "source_marker": row.get("source_marker") or "",
        "chunk_type": row.get("chunk_type") or "",
        "clause_number": row.get("clause_number") or "",
        "standard_number": row.get("standard_number") or "",
        "table_id": row.get("table_id") or "",
        "needs_review": bool(row.get("needs_review")),
        "quality_flags": _safe_json(row.get("quality_flags_json"), []),
        "evidence_text": _display_content(row),
        "candidate_relevance": "direct",
    }


def _review_template(answerable: bool) -> dict[str, Any]:
    return {
        "original_pdf_opened": False,
        "page_verified": False,
        "text_verified": False,
        "answer_verified": False,
        "no_answer_verified": False if not answerable else None,
        "reviewer_id": "",
        "reviewed_at": "",
        "manual_relevant_chunk_ids": [],
        "relevance_labels": [],
        "manual_notes": "",
    }


def _make_record(
    *,
    question_type: str,
    index: int,
    question: str,
    rows: Sequence[dict[str, Any]],
    documents: dict[str, dict[str, Any]],
    resolved: dict[str, dict[str, str]],
    db_sha256: str,
    difficulty: str,
    risk_level: str,
    answerable: bool = True,
    expected_no_answer: bool = False,
    forbidden_errors: Sequence[str] = (),
) -> dict[str, Any]:
    evidences = [_evidence(row, documents[row["document_id"]], resolved) for row in rows]
    document_ids = list(dict.fromkeys(evidence["document_id"] for evidence in evidences))
    candidate_ids = [evidence["chunk_id"] for evidence in evidences]
    return {
        "schema_version": SCHEMA_VERSION,
        "question_id": f"{TYPE_PREFIX[question_type]}-{index:03d}",
        "question_type": question_type,
        "question": question,
        "difficulty": difficulty,
        "risk_level": risk_level,
        "answerable": answerable,
        "expected_no_answer": expected_no_answer,
        "scope_document_ids": document_ids,
        "candidate_relevant_documents": document_ids,
        "candidate_relevant_chunk_ids": candidate_ids,
        "candidate_supporting_chunk_ids": [],
        "candidate_answer": _answer_excerpt(rows) if answerable else "当前限定知识库范围内无可核验答案，应拒答并说明证据不足。",
        "gold_answer": "",
        "required_facts": _required_facts(rows) if answerable else [],
        "forbidden_errors": list(forbidden_errors),
        "evidence": evidences,
        "annotation_status": "needs_human_review",
        "gold_label": False,
        "database_sha256": db_sha256,
        "review": _review_template(answerable),
    }


def _usable(row: dict[str, Any], minimum: int = 45) -> bool:
    content = _display_content(row, 1200)
    chinese = len(re.findall(r"[\u4e00-\u9fff]", content))
    return len(content) >= minimum and chinese >= max(12, minimum // 4)


def _pick_unique(
    pool: Iterable[dict[str, Any]], count: int, seed: int, salt: str, used: set[str]
) -> list[dict[str, Any]]:
    ordered = _stable_rows(pool, seed, salt)
    fresh = [row for row in ordered if row["chunk_id"] not in used]
    selected = fresh[:count]
    if len(selected) < count:
        remaining = [row for row in ordered if row not in selected]
        selected.extend(remaining[: count - len(selected)])
    if len(selected) < count:
        raise BenchmarkError(f"insufficient candidates for {salt}: need {count}, got {len(selected)}")
    used.update(row["chunk_id"] for row in selected)
    return selected


def _pick_with_document_coverage(
    pool: Iterable[dict[str, Any]], count: int, seed: int, salt: str, used: set[str]
) -> list[dict[str, Any]]:
    ordered = _stable_rows(pool, seed, salt)
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    for row in ordered:
        document_id = str(row["document_id"])
        if document_id not in covered and row["chunk_id"] not in used:
            selected.append(row)
            covered.add(document_id)
    for row in ordered:
        if len(selected) >= count:
            break
        if row not in selected and row["chunk_id"] not in used:
            selected.append(row)
    if len(selected) < count:
        remaining = [row for row in ordered if row not in selected]
        selected.extend(remaining[: count - len(selected)])
    if len(selected) < count:
        raise BenchmarkError(f"insufficient candidates for {salt}: need {count}, got {len(selected)}")
    used.update(row["chunk_id"] for row in selected)
    return selected[:count]


def _no_answer_specs() -> list[tuple[str, str]]:
    return [
        ("当前限定知识库中，木结构胶合木构件的燃烧性能等级如何确定？", "木结构防火不在当前语料范围"),
        ("当前限定知识库中，钢结构焊缝超声检测的验收等级如何划分？", "钢结构焊缝检测不在当前语料范围"),
        ("当前限定知识库中，地铁盾构隧道管片防水等级是多少？", "盾构隧道不在当前语料范围"),
        ("当前限定知识库中，港口航道疏浚工程允许超深值是多少？", "港航疏浚不在当前语料范围"),
        ("当前限定知识库中，医院洁净手术部换气次数应为多少？", "医院洁净工程不在当前语料范围"),
        ("当前限定知识库中，光伏组件的最大系统电压如何选取？", "光伏电气参数不在当前语料范围"),
        ("当前限定知识库中，铁路无缝线路锁定轨温范围是多少？", "铁路线路不在当前语料范围"),
        ("当前限定知识库中，燃气管道强度试验压力如何计算？", "燃气管道不在当前语料范围"),
        ("当前限定知识库中，电梯制动器型式试验有哪些判定指标？", "电梯型式试验不在当前语料范围"),
        ("当前限定知识库中，海上风电基础的疲劳设计寿命是多少年？", "海上风电设计不在当前语料范围"),
    ]


def build_records(
    chunks: list[dict[str, Any]],
    documents: dict[str, dict[str, Any]],
    resolved: dict[str, dict[str, str]],
    db_sha256: str,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    used: set[str] = set()

    exact_pool = [
        row for row in chunks
        if CLAUSE_RE.fullmatch(str(row.get("clause_number") or "")) and _usable(row, 45)
    ]
    exact_rows = _pick_unique(exact_pool, QUOTAS["exact_identifier"], seed, "exact", used)
    for index, row in enumerate(exact_rows, 1):
        doc = documents[row["document_id"]]
        clause = row["clause_number"]
        standard = row.get("standard_number") or doc.get("source_name") or "该文档"
        question = f"在《{standard}》中，第{clause}条主要规定或说明了什么？"
        records.append(_make_record(
            question_type="exact_identifier", index=index, question=question, rows=[row],
            documents=documents, resolved=resolved, db_sha256=db_sha256,
            difficulty="easy", risk_level="medium",
            forbidden_errors=["不得引用其他条款替代该条款", "不得改变条款中的数字、单位或否定关系"],
        ))

    paraphrase_pool = [
        row for row in chunks
        if _usable(row, 100) and _has_readable_topic(row) and not row.get("table_id") and not row.get("is_cross_page")
        and not RISK_RE.search(_display_content(row, 1200))
    ]
    paraphrase_rows = _pick_with_document_coverage(
        paraphrase_pool, QUOTAS["natural_paraphrase"], seed, "paraphrase", used
    )
    paraphrase_templates = [
        "根据《{doc}》，关于“{topic}”的核心内容是什么？",
        "如果用自然语言概括《{doc}》中的“{topic}”，应包含哪些要点？",
        "《{doc}》怎样说明“{topic}”这一问题？",
    ]
    for index, row in enumerate(paraphrase_rows, 1):
        doc_name = resolved.get(documents[row["document_id"]].get("source_sha256", ""), {}).get(
            "source_name", documents[row["document_id"]].get("source_name", "该文档")
        )
        question = paraphrase_templates[(index - 1) % len(paraphrase_templates)].format(doc=doc_name, topic=_topic(row))
        records.append(_make_record(
            question_type="natural_paraphrase", index=index, question=question, rows=[row],
            documents=documents, resolved=resolved, db_sha256=db_sha256,
            difficulty="medium", risk_level="low",
            forbidden_errors=["不得加入证据中未出现的结论", "不得混入其他文档的内容"],
        ))

    numeric_pool = [row for row in chunks if _usable(row, 70) and UNIT_RE.search(_display_content(row, 1200))]
    numeric_rows = _pick_unique(numeric_pool, QUOTAS["numeric_unit"], seed, "numeric", used)
    for index, row in enumerate(numeric_rows, 1):
        units = list(dict.fromkeys(_normalize_space(value) for value in UNIT_RE.findall(_display_content(row, 1200))))[:4]
        question = f"关于“{_topic(row)}”，原文给出了哪些关键数值或单位要求？"
        if units:
            question = f"关于“{_topic(row)}”，原文中与{'、'.join(units)}相关的数值含义是什么？"
        records.append(_make_record(
            question_type="numeric_unit", index=index, question=question, rows=[row],
            documents=documents, resolved=resolved, db_sha256=db_sha256,
            difficulty="hard", risk_level="high",
            forbidden_errors=["不得改写或四舍五入原始数值", "不得遗漏单位、上下限、百分号或比较关系"],
        ))

    table_pool = [
        row for row in chunks
        if row.get("table_id") and _usable(row, 30)
        and (UNIT_RE.search(_display_content(row, 1200)) or _display_content(row, 1200).count("|") >= 3)
        and not re.search(r"第[一二三四五六七八九十]+章|参考文献", _display_content(row, 1200))
        and not re.search(r"(?:总则|术语).*(?:一般规定|结构方案|材料)", _display_content(row, 1200))
    ]
    # Prefer different physical tables before using another row from a table.
    by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in table_pool:
        by_table[str(row["table_id"])].append(row)
    table_representatives = [max(rows, key=lambda item: len(_display_content(item, 1200))) for rows in by_table.values()]
    table_rows = _pick_unique(table_representatives, QUOTAS["table_query"], seed, "table", used)
    for index, row in enumerate(table_rows, 1):
        table_topic = _table_topic(row)
        question = f"根据表格，关于“{table_topic}”可以读出哪些项目及其对应值或分类？"
        records.append(_make_record(
            question_type="table_query", index=index, question=question, rows=[row],
            documents=documents, resolved=resolved, db_sha256=db_sha256,
            difficulty="hard", risk_level="high",
            forbidden_errors=["不得错配表头、行名和数值", "不得把目录或页眉误当成数据表结论"],
        ))

    groups: list[list[dict[str, Any]]] = []
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in chunks:
        if _usable(row, 55) and row.get("parent_id"):
            by_parent[str(row["parent_id"])].append(row)
    for rows in by_parent.values():
        pages = {row.get("primary_page") for row in rows}
        if len(rows) >= 2 and _has_readable_topic(rows[0]) and (len(pages) >= 2 or any(row.get("is_cross_page") for row in rows)):
            ordered = sorted(rows, key=lambda item: (item.get("primary_page") or 0, item["chunk_id"]))
            groups.append([ordered[0], ordered[-1]])
    groups.sort(key=lambda group: hashlib.sha256(f"{seed}:multi:{group[0]['parent_id']}".encode()).hexdigest())
    selected_groups: list[list[dict[str, Any]]] = []
    for group in groups:
        if any(row["chunk_id"] in used for row in group):
            continue
        selected_groups.append(group)
        used.update(row["chunk_id"] for row in group)
        if len(selected_groups) == QUOTAS["multi_evidence"]:
            break
    if len(selected_groups) < QUOTAS["multi_evidence"]:
        raise BenchmarkError(f"insufficient multi-evidence groups: {len(selected_groups)}")
    for index, group in enumerate(selected_groups, 1):
        question = f"综合相邻页或多个证据片段，完整说明“{_topic(group[0])}”的要求、方法或结论。"
        records.append(_make_record(
            question_type="multi_evidence", index=index, question=question, rows=group,
            documents=documents, resolved=resolved, db_sha256=db_sha256,
            difficulty="hard", risk_level="medium",
            forbidden_errors=["不得只依据单个片段遗漏后续条件", "不得合并不同文档的无关结论"],
        ))

    scope_pool = [row for row in chunks if _usable(row, 90) and _has_readable_topic(row) and not row.get("table_id")]
    scope_rows = _pick_unique(scope_pool, QUOTAS["scope_isolation"], seed, "scope", used)
    for index, row in enumerate(scope_rows, 1):
        doc = documents[row["document_id"]]
        doc_name = resolved.get(doc.get("source_sha256", ""), {}).get("source_name", doc.get("source_name", "该文档"))
        question = f"仅限《{doc_name}》回答：关于“{_topic(row)}”，文档给出的信息是什么？"
        records.append(_make_record(
            question_type="scope_isolation", index=index, question=question, rows=[row],
            documents=documents, resolved=resolved, db_sha256=db_sha256,
            difficulty="hard", risk_level="medium",
            forbidden_errors=["不得引用 scope_document_ids 之外的文档", "不得以全库相似内容替换限定文档证据"],
        ))

    all_scope_ids = sorted(documents)
    for index, (question, reason) in enumerate(_no_answer_specs(), 1):
        record = _make_record(
            question_type="no_answer", index=index, question=question, rows=[],
            documents=documents, resolved=resolved, db_sha256=db_sha256,
            difficulty="hard", risk_level="high", answerable=False, expected_no_answer=True,
            forbidden_errors=["不得用主题相近但不回答问题的内容强行作答", "不得编造标准编号、数值或条款"],
        )
        record["scope_document_ids"] = all_scope_ids
        record["no_answer_rationale_candidate"] = reason
        records.append(record)

    risk_pool = [
        row for row in chunks
        if _usable(row, 30) and _has_readable_topic(row) and RISK_RE.search(_display_content(row, 1200))
    ]
    risk_rows = _pick_with_document_coverage(
        risk_pool, QUOTAS["risk_negation_exception"], seed, "risk", used
    )
    for index, row in enumerate(risk_rows, 1):
        keyword = RISK_RE.search(_display_content(row, 1200)).group(0)
        question = f"关于“{_topic(row)}”，原文中的“{keyword}”条件、禁止项或例外边界是什么？"
        records.append(_make_record(
            question_type="risk_negation_exception", index=index, question=question, rows=[row],
            documents=documents, resolved=resolved, db_sha256=db_sha256,
            difficulty="hard", risk_level="high",
            forbidden_errors=["不得删除或反转否定词、条件词和例外关系", "不得把建议性表述改写为强制性要求或反之"],
        ))

    order = {name: index for index, name in enumerate(QUOTAS)}
    records.sort(key=lambda row: (order[row["question_type"]], row["question_id"]))
    return records


def validate_records(
    records: Sequence[dict[str, Any]], db_path: Path | None = None, require_gold: bool = False
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    counts = Counter(str(row.get("question_type")) for row in records)
    ids = [str(row.get("question_id") or "") for row in records]
    if len(records) != sum(QUOTAS.values()):
        errors.append(f"record_count: expected {sum(QUOTAS.values())}, got {len(records)}")
    if len(ids) != len(set(ids)):
        errors.append("question_id values are not unique")
    for question_type, expected in QUOTAS.items():
        if counts[question_type] != expected:
            errors.append(f"quota {question_type}: expected {expected}, got {counts[question_type]}")

    db_chunk_ids: set[str] = set()
    db_document_ids: set[str] = set()
    if db_path is not None:
        with _connect_read_only(db_path) as db:
            db_chunk_ids = {row[0] for row in db.execute("SELECT chunk_id FROM pipeline_chunks")}
            db_document_ids = {row[0] for row in db.execute("SELECT document_id FROM pipeline_documents")}

    for row in records:
        qid = str(row.get("question_id") or "<missing>")
        if row.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{qid}: wrong schema_version")
        no_answer = bool(row.get("expected_no_answer"))
        candidate_ids = list(row.get("candidate_relevant_chunk_ids") or [])
        evidence = list(row.get("evidence") or [])
        if no_answer and (candidate_ids or evidence or row.get("answerable")):
            errors.append(f"{qid}: no-answer invariant violated")
        if not no_answer and (not candidate_ids or not evidence):
            errors.append(f"{qid}: answerable record lacks direct evidence")
        if not require_gold:
            if row.get("gold_label") is not False or row.get("annotation_status") != "needs_human_review":
                errors.append(f"{qid}: automatic record was promoted to Gold")
        if db_chunk_ids:
            unknown = sorted(set(candidate_ids) - db_chunk_ids)
            if unknown:
                errors.append(f"{qid}: unknown chunk IDs: {unknown}")
            unknown_docs = sorted(set(row.get("scope_document_ids") or []) - db_document_ids)
            if unknown_docs:
                errors.append(f"{qid}: unknown scope document IDs: {unknown_docs}")
        for item in evidence:
            if not item.get("page_numbers") or not item.get("source_marker"):
                errors.append(f"{qid}: evidence lacks page provenance")
            if not item.get("source_sha256"):
                warnings.append(f"{qid}: source SHA-256 is empty")

    evidence_ids = [
        chunk_id
        for row in records
        for chunk_id in (row.get("candidate_relevant_chunk_ids") or [])
    ]
    duplicate_count = len(evidence_ids) - len(set(evidence_ids))
    return {
        "valid": not errors,
        "record_count": len(records),
        "type_counts": dict(sorted(counts.items())),
        "unique_direct_evidence": len(set(evidence_ids)),
        "direct_evidence_reuse_count": duplicate_count,
        "direct_evidence_reuse_ratio": round(duplicate_count / len(evidence_ids), 6) if evidence_ids else 0.0,
        "errors": errors,
        "warnings": warnings,
    }


def _write_review_csv(path: Path, records: Sequence[dict[str, Any]]) -> None:
    fields = [
        "question_id", "question_type", "question", "difficulty", "risk_level", "answerable",
        "scope_document_ids", "candidate_relevant_chunk_ids", "source_pages", "candidate_answer",
        "required_facts", "gold_answer", "manual_relevant_chunk_ids", "relevance_labels",
        "original_pdf_opened", "page_verified", "text_verified", "answer_verified",
        "no_answer_verified", "reviewer_id", "reviewed_at", "manual_notes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            review = record["review"]
            pages = [
                {"document_id": evidence["document_id"], "pages": evidence["page_numbers"]}
                for evidence in record["evidence"]
            ]
            writer.writerow({
                "question_id": record["question_id"],
                "question_type": record["question_type"],
                "question": record["question"],
                "difficulty": record["difficulty"],
                "risk_level": record["risk_level"],
                "answerable": record["answerable"],
                "scope_document_ids": json.dumps(record["scope_document_ids"], ensure_ascii=False),
                "candidate_relevant_chunk_ids": json.dumps(record["candidate_relevant_chunk_ids"], ensure_ascii=False),
                "source_pages": json.dumps(pages, ensure_ascii=False),
                "candidate_answer": record["candidate_answer"],
                "required_facts": json.dumps(record["required_facts"], ensure_ascii=False),
                "gold_answer": record["gold_answer"],
                "manual_relevant_chunk_ids": json.dumps(review["manual_relevant_chunk_ids"], ensure_ascii=False),
                "relevance_labels": json.dumps(review["relevance_labels"], ensure_ascii=False),
                "original_pdf_opened": review["original_pdf_opened"],
                "page_verified": review["page_verified"],
                "text_verified": review["text_verified"],
                "answer_verified": review["answer_verified"],
                "no_answer_verified": review["no_answer_verified"],
                "reviewer_id": review["reviewer_id"],
                "reviewed_at": review["reviewed_at"],
                "manual_notes": review["manual_notes"],
            })


def _write_catalog(path: Path, records: Sequence[dict[str, Any]]) -> None:
    lines = [
        "# 正式 RAG 基准候选集目录", "",
        "> 自动生成内容尚未经过逐页人工复核，因此不是 Gold。候选答案仅用于加速标注。", "",
        "| ID | 类型 | 难度 | 问题 | 证据页 |", "|---|---|---|---|---|",
    ]
    for record in records:
        pages = ", ".join(
            f"{e['source_name']}:{'/'.join(map(str, e['page_numbers']))}" for e in record["evidence"]
        ) or "无答案题，待人工确认范围"
        question = str(record["question"]).replace("|", "\\|")
        lines.append(f"| {record['question_id']} | {record['question_type']} | {record['difficulty']} | {question} | {pages} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _review_guide() -> str:
    return r"""# 人工复核与 Gold 发布说明

本目录的 `candidates.jsonl` 是正式基准候选包，但不是 Gold。自动检索到的 chunk、候选答案和必备事实都可能继承 OCR、表格解析或切片错误。

## 逐题复核

1. 复制 `review_template.jsonl` 为新的版本化审核文件，不要覆盖 `candidates.jsonl`。
2. 按 `evidence[].source_path` 打开原 PDF，并跳转到 `page_numbers`；不要只看 SQLite 文本。
3. 核对页码、条款号、表头/行列、数字、单位、上下限、否定词和例外条件。
4. 将每个候选 chunk 标为 `direct`、`supporting`、`context_only` 或 `irrelevant`。
5. 在 `review.manual_relevant_chunk_ids` 中只保留能够直接回答问题的 chunk；填写 `gold_answer`。
6. 无答案题必须检查 manifest 所列全部 scope 文档，确认确实无答案，再将 `no_answer_verified` 设为 true。
7. 填写 reviewer_id、reviewed_at 和 notes，并将四项 PDF/文本/答案核验布尔值设为 true。

## 发布 Gold

```powershell
D:\anaconda\envs\YOLO11-HAI\python.exe scripts\formal_rag_benchmark.py publish `
  --review benchmarks\rag\formal_v1\review_working.jsonl `
  --db knowledge_pipeline\test\results\index\pipeline.sqlite3 `
  --output benchmarks\rag\formal_v1\gold_v1
```

发布器会拒绝缺少原 PDF 核验、答案核验、人工直接证据、相关性标签或审核人信息的记录。建议对外报告前采用双人独立标注与冲突仲裁。
"""


def generate(
    db_path: Path,
    output_dir: Path,
    source_roots: Sequence[Path],
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    db_path = db_path.resolve()
    db_hash_before = _sha256(db_path)
    legacy_files = [PROJECT_ROOT / "benchmarks" / "rag" / name for name in ("document_benchmark.jsonl", "retrieval_qa_benchmark.jsonl")]
    legacy_hashes_before = {str(path): _sha256(path) for path in legacy_files if path.is_file()}
    chunks, documents = _load_snapshot(db_path)
    resolved = _resolve_sources(documents, source_roots)
    records = build_records(chunks, documents, resolved, db_hash_before, seed)
    validation = validate_records(records, db_path)
    if not validation["valid"]:
        raise BenchmarkError("generated candidate validation failed: " + "; ".join(validation["errors"]))

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "candidates.jsonl", records)
    _write_jsonl(output_dir / "review_template.jsonl", records)
    _write_review_csv(output_dir / "review_template.csv", records)
    _write_catalog(output_dir / "catalog.md", records)
    (output_dir / "REVIEW_GUIDE.md").write_text(_review_guide(), encoding="utf-8")

    source_inventory = []
    for document_id, doc in sorted(documents.items()):
        source_sha = str(doc.get("source_sha256") or "")
        source = resolved.get(source_sha, {})
        source_inventory.append({
            "document_id": document_id,
            "source_name": source.get("source_name") or doc.get("source_name") or "",
            "source_path": source.get("source_path") or doc.get("source_path") or "",
            "source_sha256": source_sha,
            "original_pdf_resolved": bool(source),
            "page_count": doc.get("page_count"),
            "retrieval_chunk_count": doc.get("retrieval_chunk_count"),
            "quality_score": doc.get("quality_score"),
        })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_status": "formal_candidates_pending_human_review",
        "generated_at_utc": _utc_now(),
        "seed": seed,
        "database_path": str(db_path),
        "database_sha256": db_hash_before,
        "database_read_only": True,
        "record_count": len(records),
        "type_quotas": QUOTAS,
        "type_counts": validation["type_counts"],
        "annotation_status": "needs_human_review",
        "gold_label": False,
        "source_documents": source_inventory,
        "source_pdf_resolution_count": sum(item["original_pdf_resolved"] for item in source_inventory),
        "candidate_file_sha256": _sha256(output_dir / "candidates.jsonl"),
        "review_template_file_sha256": _sha256(output_dir / "review_template.jsonl"),
        "legacy_benchmarks_preserved": True,
    }
    _write_json(output_dir / "manifest.json", manifest)
    _write_json(output_dir / "coverage_report.json", validation)
    report_lines = [
        "# 基准覆盖报告", "", f"- 状态：{manifest['dataset_status']}",
        f"- 题目数：{len(records)}", f"- 唯一直接证据 chunk：{validation['unique_direct_evidence']}",
        f"- 直接证据复用率：{validation['direct_evidence_reuse_ratio']:.2%}",
        f"- 已解析到原 PDF：{manifest['source_pdf_resolution_count']}/{len(source_inventory)}", "", "## 类型分布", "",
        "| 类型 | 数量 |", "|---|---:|",
    ]
    report_lines.extend(f"| {name} | {validation['type_counts'].get(name, 0)} |" for name in QUOTAS)
    report_lines.extend(["", "## 质量边界", "", "- 所有记录均为 `needs_human_review` / `gold_label: false`。", "- 候选答案来自索引证据，不等于原 PDF 人工核验答案。", "- 只有通过 REVIEW_GUIDE 所述发布门禁的审核副本才能成为 Gold。", ""])
    (output_dir / "coverage_report.md").write_text("\n".join(report_lines), encoding="utf-8")

    if _sha256(db_path) != db_hash_before:
        raise BenchmarkError("input database changed during generation")
    legacy_hashes_after = {str(path): _sha256(path) for path in legacy_files if path.is_file()}
    if legacy_hashes_after != legacy_hashes_before:
        raise BenchmarkError("legacy benchmark assets changed during generation")
    return manifest


def _publication_errors(records: Sequence[dict[str, Any]], db_path: Path) -> list[str]:
    errors: list[str] = []
    with _connect_read_only(db_path) as db:
        chunk_ids = {row[0] for row in db.execute("SELECT chunk_id FROM pipeline_chunks")}
    for row in records:
        qid = str(row.get("question_id") or "<missing>")
        review = row.get("review") or {}
        for field in ("original_pdf_opened", "page_verified", "text_verified", "answer_verified"):
            if review.get(field) is not True:
                errors.append(f"{qid}: review.{field} must be true")
        if not str(review.get("reviewer_id") or "").strip():
            errors.append(f"{qid}: reviewer_id is required")
        if not str(review.get("reviewed_at") or "").strip():
            errors.append(f"{qid}: reviewed_at is required")
        labels = list(review.get("relevance_labels") or [])
        if row.get("expected_no_answer"):
            if review.get("no_answer_verified") is not True:
                errors.append(f"{qid}: no_answer_verified must be true")
        else:
            manual_ids = list(review.get("manual_relevant_chunk_ids") or [])
            if not str(row.get("gold_answer") or "").strip():
                errors.append(f"{qid}: gold_answer is required")
            if not manual_ids:
                errors.append(f"{qid}: manual_relevant_chunk_ids is required")
            unknown = sorted(set(manual_ids) - chunk_ids)
            if unknown:
                errors.append(f"{qid}: manual evidence does not exist: {unknown}")
            direct = {str(item.get("chunk_id")) for item in labels if item.get("label") == "direct"}
            if not set(manual_ids).issubset(direct):
                errors.append(f"{qid}: every manual relevant chunk must have a direct relevance label")
        if not labels and not row.get("expected_no_answer"):
            errors.append(f"{qid}: relevance_labels is required")
    return errors


def publish(review_path: Path, db_path: Path, output_dir: Path) -> dict[str, Any]:
    records = _read_jsonl(review_path)
    errors = _publication_errors(records, db_path)
    if errors:
        raise BenchmarkError("Gold publication rejected:\n- " + "\n- ".join(errors))
    db_sha = _sha256(db_path)
    gold_rows: list[dict[str, Any]] = []
    for record in records:
        item = json.loads(json.dumps(record, ensure_ascii=False))
        item["annotation_status"] = "gold_reviewed"
        item["gold_label"] = True
        item["relevant_chunk_ids"] = list(item["review"].get("manual_relevant_chunk_ids") or [])
        item["relevant_documents"] = list(dict.fromkeys(
            evidence["document_id"] for evidence in item.get("evidence", [])
            if evidence.get("chunk_id") in item["relevant_chunk_ids"]
        ))
        gold_rows.append(item)
    output_dir.mkdir(parents=True, exist_ok=True)
    gold_path = output_dir / "gold.jsonl"
    _write_jsonl(gold_path, gold_rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_status": "gold_reviewed",
        "published_at_utc": _utc_now(),
        "record_count": len(gold_rows),
        "database_path": str(db_path.resolve()),
        "database_sha256": db_sha,
        "review_source_path": str(review_path.resolve()),
        "review_source_sha256": _sha256(review_path),
        "gold_file_sha256": _sha256(gold_path),
        "reviewers": sorted({str(row["review"]["reviewer_id"]) for row in gold_rows}),
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    generate_parser = subparsers.add_parser("generate", help="generate the 150-question candidate package")
    generate_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    generate_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    generate_parser.add_argument("--source-root", type=Path, action="append", default=[])
    generate_parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    validate_parser = subparsers.add_parser("validate", help="validate a candidate JSONL")
    validate_parser.add_argument("--input", type=Path, default=DEFAULT_OUTPUT / "candidates.jsonl")
    validate_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    publish_parser = subparsers.add_parser("publish", help="publish a fully human-reviewed Gold JSONL")
    publish_parser.add_argument("--review", type=Path, required=True)
    publish_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    publish_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    command = args.command or "generate"
    try:
        if command == "generate":
            roots = args.source_root or [
                PROJECT_ROOT / "knowledge_pipeline" / "test" / "测试文档",
                PROJECT_ROOT / "knowledge_base" / "source_files",
            ]
            result = generate(args.db, args.output, roots, args.seed)
        elif command == "validate":
            result = validate_records(_read_jsonl(args.input), args.db)
            if not result["valid"]:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
        else:
            result = publish(args.review, args.db, args.output)
    except BenchmarkError as exc:
        print(str(exc))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
