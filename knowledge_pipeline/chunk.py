"""Structure-aware, provenance-preserving chunking for converted documents."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from html.parser import HTMLParser

from .contracts import (
    BlockRecord, ChunkRecord, DocumentConversion, ImageRecord, PageRecord,
    QualityReport, StageStatus, TableRecord, CHUNK_SCHEMA_VERSION,
    read_json, write_json,
)

CHUNK_STAGE_VERSION = "chunk.v2"
DEFAULT_CHILD_TOKENS = 520
DEFAULT_PARENT_TOKENS = 1600
DEFAULT_OVERLAP_TOKENS = 80
_CLAUSE_RE = re.compile(r"^(?:第\s*)?(\d+(?:\.\d+){1,4})(?:\s*条)?(?:[\s、:：.)）-].*)?$", re.I)
_HEADING_RE = re.compile(r"^(?:第\s*\d+\s*[章节篇]|\d+(?:\.\d+){0,3}\s+|附录).{0,120}$")
_LIST_RE = re.compile(r"^(?:（\d+）|\d+[.)）]|[①②③④⑤⑥⑦⑧⑨⑩]|[A-Za-z][.)）])\s+")


@dataclass
class ChunkConfig:
    child_max_tokens: int = DEFAULT_CHILD_TOKENS
    parent_max_tokens: int = DEFAULT_PARENT_TOKENS
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS
    table_rows_per_child: int = 12
    include_parents: bool = True
    filter_boilerplate: bool = True


@dataclass
class StructureUnit:
    unit_id: str
    text: str
    block_ids: list[str]
    page_numbers: list[int]
    heading_path: list[str]
    clause_number: str = ""
    chunk_type: str = "paragraph"
    extraction_methods: list[str] | None = None
    quality_flags: list[str] | None = None
    is_cross_page: bool = False


def _clean(value: Any, *, lines: bool = False) -> str:
    text = str(value or "").replace("\u00a0", " ").replace("\r", "")
    if lines:
        return "\n".join(re.sub(r"[ \t]+", " ", x).strip() for x in text.split("\n") if x.strip()).strip()
    return re.sub(r"\s+", " ", text).strip()


def _usable(text: str) -> bool:
    return bool(re.search(r"[\w\u3400-\u9fff]", text or ""))


def _token_count(text: str) -> int:
    value = str(text or "")
    return max(0, len(re.findall(r"[\u3400-\u9fff]", value)) + len(re.findall(r"[A-Za-z0-9_]+", value)) + (len(re.findall(r"[^\w\u3400-\u9fff\s]", value)) + 3) // 4)


def _split_text(text: str, limit: int, overlap: int = 0) -> list[str]:
    if not text.strip():
        return []
    try:
        import semchunk
        return [str(x).strip() for x in semchunk.chunk(text, limit, _token_count, overlap=overlap) if str(x).strip()]
    except Exception:
        parts = [x.strip() for x in re.split(r"(?<=[。！？；.!?;])\s*|\n+", text) if x.strip()]
        result: list[str] = []
        current = ""
        for part in parts or [text]:
            candidate = f"{current} {part}".strip() if current else part
            if current and _token_count(candidate) > limit:
                result.append(current)
                current = f"{current[-max(1, overlap * 2):]} {part}".strip()
            else:
                current = candidate
        if current:
            result.append(current)
        bounded: list[str] = []
        for item in result:
            if _token_count(item) <= limit:
                bounded.append(item)
            else:
                step = max(1, limit * 2); stride = max(1, step - overlap * 2)
                bounded.extend(item[i:i + step].strip() for i in range(0, len(item), stride))
        return [x for x in bounded if x]


def _models(payload: dict[str, Any], key: str, cls: Any) -> list[Any]:
    result = []; fields = getattr(cls, "__dataclass_fields__", {})
    for raw in payload.get(key) or []:
        if isinstance(raw, cls): result.append(raw); continue
        if isinstance(raw, dict):
            try: result.append(cls(**{k: raw[k] for k in fields if k in raw}))
            except TypeError: pass
    return result


def _legacy_table_html(raw: dict[str, Any]) -> str:
    """Convert pre-v3 rows/markdown to the v3 canonical HTML body."""
    value = str(raw.get("html") or "").strip()
    if value:
        return value
    rows = raw.get("rows")
    if not isinstance(rows, list) or not rows:
        markdown = str(raw.get("markdown") or "")
        rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in markdown.splitlines() if line.strip()]
    if not rows:
        return ""
    parts = ["<table>"]
    for row in rows:
        if isinstance(row, (list, tuple)):
            parts.append("<tr>" + "".join(f'<td>{html.escape(str(cell or ""))}</td>' for cell in row) + "</tr>")
    parts.append("</table>")
    return "".join(parts) if len(parts) > 2 else ""


def conversion_from_payload(payload: dict[str, Any]) -> DocumentConversion:
    status_raw = payload.get("status") or {}; quality_raw = payload.get("quality_report") or {}
    status = StageStatus(**{k: status_raw[k] for k in StageStatus.__dataclass_fields__ if k in status_raw})
    quality = QualityReport(**{k: quality_raw[k] for k in QualityReport.__dataclass_fields__ if k in quality_raw})
    raw_tables = payload.get("tables") or []
    tables: list[TableRecord] = []
    for raw in raw_tables:
        if isinstance(raw, TableRecord):
            tables.append(raw)
        elif isinstance(raw, dict):
            item = dict(raw)
            item["html"] = _legacy_table_html(item)
            try:
                tables.append(TableRecord(**{k: item[k] for k in TableRecord.__dataclass_fields__ if k in item}))
            except TypeError:
                continue
    return DocumentConversion(
        document_id=str(payload.get("document_id", "")), source_path=str(payload.get("source_path", "")), source_name=str(payload.get("source_name", "")), source_sha256=str(payload.get("source_sha256", "")), extension=str(payload.get("extension", "")),
        blocks=_models(payload, "blocks", BlockRecord), pages=_models(payload, "pages", PageRecord), images=_models(payload, "images", ImageRecord), tables=tables, visual_regions=[dict(item) for item in (payload.get("visual_regions") or []) if isinstance(item, dict)], quality_report=quality, status=status, metadata=dict(payload.get("metadata") or {}), schema_version=str(payload.get("schema_version", "knowledge-conversion.v2")),
    )


def _is_projection(block: BlockRecord) -> bool:
    return block.block_type == "document_projection" or (block.location == "document:docling" and block.extraction_method == "layout:docling")


def _dedupe_blocks(conversion: DocumentConversion) -> list[BlockRecord]:
    blocks = list(conversion.blocks)
    if any(b.page_number and b.extraction_method == "layout:docling" and not _is_projection(b) for b in blocks): blocks = [b for b in blocks if not _is_projection(b)]
    seen: set[str] = set(); result: list[BlockRecord] = []
    for block in blocks:
        text = _clean(block.text, lines=block.block_type in {"ocr", "ocr_line"})
        if not _usable(text): continue
        key = f"{block.block_id}|{block.location}|{hashlib.sha1(text.encode()).hexdigest()}"
        if key in seen: continue
        seen.add(key); result.append(block)
    return sorted(result, key=lambda b: (b.page_number or 10**9, (b.bbox or [0, 0, 0, 0])[1], (b.bbox or [0, 0, 0, 0])[0], b.location))


def _boilerplate(blocks: list[BlockRecord]) -> set[str]:
    pages = {b.page_number for b in blocks if b.page_number}
    if len(pages) < 3: return set()
    occurrences: dict[str, set[int]] = {}
    for block in blocks:
        text = _clean(block.text)
        if 2 <= len(text) <= 40 and block.page_number: occurrences.setdefault(text, set()).add(block.page_number)
    return {text for text, values in occurrences.items() if len(values) / len(pages) >= .30}


def _heading_clause(text: str) -> tuple[str, str]:
    value = _clean(text); match = _CLAUSE_RE.match(value); clause = match.group(1) if match else ""
    terminal = value.endswith(("。", "！", "？", "；", ".", "!", "?", ";"))
    substantive_clause = bool(clause and len(value) > 24)
    heading = value if (not terminal and not substantive_clause and (_HEADING_RE.match(value) or (len(value) <= 80 and value.startswith("第")))) else ""
    return heading, clause


def build_structure_units(conversion: DocumentConversion, config: ChunkConfig | None = None) -> list[StructureUnit]:
    cfg = config or ChunkConfig(); blocks = _dedupe_blocks(conversion); ignored = _boilerplate(blocks) if cfg.filter_boilerplate else set()
    units: list[StructureUnit] = []; current: StructureUnit | None = None; headings: list[str] = []; index = 0; last_page: int | None = None
    for block in blocks:
        text = _clean(block.text, lines=block.block_type in {"ocr", "ocr_line"})
        if not text or text in ignored: continue
        heading, clause = _heading_clause(text); label = str(block.metadata.get("docling_label", "")).casefold()
        if label in {"page_footer", "page_header", "header", "footer"}: continue
        if heading or "heading" in label or "title" in label:
            if current: units.append(current); current = None
            headings = (headings + [text])[-4:]
            last_page = block.page_number
            continue
        effective_clause = clause
        kind = "list" if _LIST_RE.match(text) else ("image_ocr" if block.block_type.startswith("ocr") else "paragraph")
        same_page = current is not None and block.page_number == last_page
        contiguous_continuation = current is not None and block.page_number is not None and last_page is not None and block.page_number == last_page + 1 and not current.text.endswith(("。", "！", "？", "；", ".", "!", "?", ";"))
        merge = current is not None and current.heading_path == headings and current.clause_number == effective_clause and current.chunk_type == kind and (same_page or bool(effective_clause) or contiguous_continuation)
        if effective_clause and current and current.clause_number != effective_clause: merge = False
        if not merge:
            if current: units.append(current)
            index += 1; current = StructureUnit(f"{conversion.document_id}:unit:{index}", text, [block.block_id], [block.page_number] if block.page_number else [], list(headings), effective_clause, kind, [block.extraction_method], ["possible_encoding_anomaly"] if block.metadata.get("encoding_anomaly") else [])
        else:
            assert current; current.text += ("\n" if kind == "list" else " ") + text; current.block_ids.append(block.block_id)
            if block.page_number and block.page_number not in current.page_numbers: current.page_numbers.append(block.page_number)
            if block.extraction_method not in (current.extraction_methods or []): current.extraction_methods.append(block.extraction_method)
            if len(current.page_numbers) > 1: current.is_cross_page = True; current.quality_flags.append("cross_page_merge") if "cross_page_merge" not in current.quality_flags else None
        last_page = block.page_number or last_page
    if current: units.append(current)
    return units


def _marker(document_id: str, pages: Iterable[int], location: str) -> str:
    values = sorted({int(x) for x in pages if x})
    suffix = f"page:{values[0]}" if len(values) == 1 else f"page:{values[0]}-{values[-1]}" if values and values == list(range(values[0], values[-1] + 1)) else f"pages:{','.join(map(str, values))}" if values else location
    return f"[KB:{document_id}:{suffix}]"


def _contextual(conversion: DocumentConversion, unit: StructureUnit, body: str, marker: str) -> str:
    name = conversion.source_name or Path(conversion.source_path).name; match = re.search(r"\b(?:GB|JGJ|CECS|DBJ)\s*[-]?\s*\d{2,6}(?:[-—]\d{4})?\b", name, re.I); standard = match.group(0).replace(" ", "") if match else ""
    lines = [f"文档：{name}"] + ([f"标准编号：{standard}"] if standard else []) + ([f"章节路径：{' > '.join(unit.heading_path)}"] if unit.heading_path else []) + ([f"条款：{unit.clause_number}"] if unit.clause_number else []) + ["内容：", body.strip(), f"来源：{marker}"]
    return "\n".join(lines)


def _record(conversion: DocumentConversion, unit: StructureUnit, text: str, *, chunk_id: str, location: str, parent_id: str, level: str, role: str, part: int = 0, extra: dict[str, Any] | None = None) -> ChunkRecord:
    marker = _marker(conversion.document_id, unit.page_numbers, location); contextual = _contextual(conversion, unit, text, marker)
    metadata = {"chunk_level": level, "retrieval_role": role, "chunk_type": unit.chunk_type, "parent_chunk_id": parent_id, "block_ids": unit.block_ids, "page_numbers": unit.page_numbers, "heading_path": unit.heading_path, "clause_number": unit.clause_number, "extraction_methods": unit.extraction_methods or [], "quality_flags": unit.quality_flags or [], "is_cross_page": unit.is_cross_page, "part": part, "token_count": _token_count(text), "char_count": len(text), "text_raw": unit.text, "text_search": _clean(text), "text_contextualized": contextual}
    if extra: metadata.update(extra)
    return ChunkRecord(chunk_id, conversion.document_id, location, contextual, parent_id, marker, metadata)


class _TableRowsParser(HTMLParser):
    """Small dependency-free parser for the converter's sanitized table HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "tr":
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None


def _table_rows(table: TableRecord) -> list[list[str]]:
    parser = _TableRowsParser()
    try:
        parser.feed(str(table.html or ""))
        parser.close()
    except Exception:
        parser.rows = []
    rows = [[_clean(cell) for cell in row] for row in parser.rows if any(_clean(cell) for cell in row)]
    if not rows:
        # Legacy objects created in-process may still expose rows/markdown even
        # though the v3 dataclass no longer declares those fields.
        legacy_rows = getattr(table, "rows", None)
        if isinstance(legacy_rows, list):
            rows = [[_clean(cell) for cell in row] for row in legacy_rows if isinstance(row, (list, tuple)) and any(_clean(cell) for cell in row)]
        if not rows:
            markdown = str(getattr(table, "markdown", "") or "")
            rows = [[_clean(cell) for cell in line.strip().strip("|").split("|")] for line in markdown.splitlines() if line.strip()]
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def _table_matrix(table: TableRecord) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(_clean(cell).casefold() for cell in row)
        for row in _table_rows(table)
    )


def _bbox_iou(first: list[float] | None, second: list[float] | None) -> float:
    if not first or not second or len(first) != 4 or len(second) != 4:
        return 0.0
    ax0, ay0, ax1, ay1 = map(float, first)
    bx0, by0, bx1, by1 = map(float, second)
    intersection_width = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    intersection_height = max(0.0, min(ay1, by1) - max(ay0, by0))
    intersection = intersection_width * intersection_height
    first_area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    second_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = first_area + second_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _cross_backend_table_equivalent(first: TableRecord, second: TableRecord) -> bool:
    if first.extraction_method == second.extraction_method:
        return False
    if first.page_number != second.page_number or _bbox_iou(first.bbox, second.bbox) < 0.85:
        return False
    first_matrix = _table_matrix(first)
    second_matrix = _table_matrix(second)
    if not first_matrix or not second_matrix:
        return False
    if len(first_matrix) != len(second_matrix) or any(
        len(first_row) != len(second_row)
        for first_row, second_row in zip(first_matrix, second_matrix)
    ):
        return False
    similarities = [
        1.0 if first_cell == second_cell else difflib.SequenceMatcher(None, first_cell, second_cell).ratio()
        for first_row, second_row in zip(first_matrix, second_matrix)
        for first_cell, second_cell in zip(first_row, second_row)
    ]
    return bool(similarities) and min(similarities) >= 0.86 and sum(similarities) / len(similarities) >= 0.985


def _tables_equivalent(first: TableRecord, second: TableRecord) -> bool:
    if first.page_number != second.page_number:
        return False
    first_matrix = _table_matrix(first)
    second_matrix = _table_matrix(second)
    if not first_matrix or first_matrix != second_matrix:
        return _cross_backend_table_equivalent(first, second)
    if first.bbox and second.bbox:
        return _bbox_iou(first.bbox, second.bbox) >= 0.85
    # Geometry-free exact duplicates remain mergeable for legacy conversion
    # payloads; when both regions are known, location is part of table identity.
    return True


def _canonical_tables(tables: list[TableRecord]) -> list[TableRecord]:
    result: list[TableRecord] = []
    for table in sorted(tables, key=lambda x: (x.page_number or 10**9, 0 if x.extraction_method == "table:docling" else 1, x.table_id)):
        rows = _table_rows(table)
        if not rows:
            continue
        duplicate = None
        for existing in result:
            if _tables_equivalent(existing, table):
                duplicate = existing
                break
        if duplicate:
            duplicate.metadata.setdefault("alternative_sources", []).append({
                "table_id": table.table_id,
                "extraction_method": table.extraction_method,
                "bbox": table.bbox,
                "html": str(table.html or ""),
                "metadata": dict(table.metadata or {}),
            })
        else:
            result.append(table)
    return result


def _table_chunks(conversion: DocumentConversion, cfg: ChunkConfig) -> list[ChunkRecord]:
    output: list[ChunkRecord] = []
    for number, table in enumerate(_canonical_tables(conversion.tables), 1):
        rows = _table_rows(table)
        if not rows: continue
        unit = StructureUnit(f"table:{number}", "\n".join("｜".join(r) for r in rows), [], [table.page_number] if table.page_number else [], [], chunk_type="table", quality_flags=["needs_review"] if table.needs_review else [])
        base = f"{conversion.document_id}:table:{number}"; header = "｜".join(rows[0]); body = "\n".join("｜".join(r) for r in rows)
        output.append(_record(conversion, unit, f"表头：{header}\n{body}", chunk_id=f"{base}:parent", location=f"page:{table.page_number}:table:{number}", parent_id=f"{base}:parent", level="parent", role="context_only", extra={"table_id": table.table_id, "table_source": table.extraction_method, "bbox": table.bbox, "needs_review": table.needs_review}))
        for start in range(1, len(rows), max(1, cfg.table_rows_per_child)):
            selected = rows[start:start + cfg.table_rows_per_child]
            output.append(_record(conversion, unit, f"表头：{header}\n" + "\n".join("｜".join(r) for r in selected), chunk_id=f"{base}:child:{start}", location=f"page:{table.page_number}:table:{number}:rows:{start}-{start + len(selected) - 1}", parent_id=f"{base}:parent", level="child", role="retrieval", part=start, extra={"chunk_type": "table_row", "table_id": table.table_id, "table_source": table.extraction_method, "bbox": table.bbox, "needs_review": table.needs_review, "header_repeated": True}))
    return output


def _image_references(conversion: DocumentConversion) -> list[dict[str, Any]]:
    references = [{"image_id": image.image_id, "page_number": image.page_number, "asset_path": image.asset_path, "image_hash": image.image_hash, "needs_review": True} for image in conversion.images if not _usable(_clean(image.ocr_text or image.vision_search or image.vision_summary))]
    for region in conversion.visual_regions:
        text = _clean(region.get("ocr_text") or region.get("vision_search") or region.get("vision_summary"))
        if not _usable(text):
            references.append({"region_id": str(region.get("region_id") or ""), "page_number": region.get("page_number"), "bbox": region.get("bbox"), "region_type": str(region.get("region_type") or "visual"), "needs_review": True})
    return references


def _table_diagnostics(conversion: DocumentConversion) -> list[dict[str, Any]]:
    return [{"table_id": table.table_id, "page_number": table.page_number, "bbox": table.bbox, "extraction_method": table.extraction_method, "needs_review": True, "quality_flags": ["table_candidate_without_cells"]} for table in conversion.tables if not _table_rows(table)]


def _image_chunks(conversion: DocumentConversion) -> list[ChunkRecord]:
    """Emit searchable image OCR only when the conversion contains text."""
    output: list[ChunkRecord] = []
    for image in conversion.images:
        text = _clean("\n".join(x for x in (image.ocr_text, image.vision_search or image.vision_summary) if x))
        if not _usable(text):
            continue
        unit = StructureUnit(
            unit_id=image.image_id,
            text=text,
            block_ids=[],
            page_numbers=[image.page_number] if image.page_number else [],
            heading_path=[],
            chunk_type="image_ocr",
            extraction_methods=[image.extraction_method],
        )
        output.append(
            _record(
                conversion,
                unit,
                text,
                chunk_id=f"{image.image_id}:ocr",
                location=f"page:{image.page_number}:image:{image.image_id}",
                parent_id=image.image_id,
                level="child",
                role="retrieval",
                extra={
                    "image_id": image.image_id,
                    "asset_path": image.asset_path,
                    "width": image.width,
                    "height": image.height,
                    "image_hash": image.image_hash,
                    "vision_summary": image.vision_summary,
                    "vision_search": image.vision_search,
                    "model_generated": bool(image.vision_summary or image.vision_search),
                },
            )
        )
    return output


def _visual_region_chunks(conversion: DocumentConversion) -> list[ChunkRecord]:
    output: list[ChunkRecord] = []
    for index, region in enumerate(conversion.visual_regions):
        ocr_text = _clean(region.get("ocr_text"))
        vision_search = _clean(region.get("vision_search") or region.get("vision_summary"))
        text = _clean("\n".join(x for x in (ocr_text, vision_search) if x))
        if not _usable(text):
            continue
        page = int(region.get("page_number") or 0)
        region_id = str(region.get("region_id") or f"{conversion.document_id}:visual:{index}")
        unit = StructureUnit(region_id, text, [], [page] if page else [], [], chunk_type="image_ocr", extraction_methods=[str(region.get("extraction_method") or "vision")], quality_flags=["model_generated"] if vision_search and not ocr_text else [])
        output.append(_record(conversion, unit, text, chunk_id=f"{region_id}:ocr", location=f"page:{page}:visual:{region_id}", parent_id=region_id, level="child", role="retrieval", extra={"region_id": region_id, "region_type": str(region.get("region_type") or "visual"), "bbox": region.get("bbox"), "ocr_text": ocr_text, "vision_raw": str(region.get("vision_raw") or ""), "vision_summary": str(region.get("vision_summary") or ""), "vision_search": vision_search, "model_generated": bool(vision_search), "needs_review": bool(vision_search)}))
    return output


def validate_chunks(chunks: list[ChunkRecord]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []; warnings: list[dict[str, str]] = []; seen: dict[str, tuple[str, str]] = {}
    for chunk in chunks:
        text = _clean(chunk.text); digest = hashlib.sha1(text.encode()).hexdigest()
        if not text: errors.append({"chunk_id": chunk.chunk_id, "reason": "empty_text"})
        if not chunk.source_marker: errors.append({"chunk_id": chunk.chunk_id, "reason": "missing_provenance"})
        if digest in seen:
            previous_id, previous_parent = seen[digest]
            # A short unit fitting in one child is intentionally repeated in
            # its context parent; this is not source duplication.
            if previous_parent != chunk.parent_id:
                warnings.append({"chunk_id": chunk.chunk_id, "reason": f"duplicate_of:{previous_id}"})
        else: seen[digest] = (chunk.chunk_id, chunk.parent_id)
        if chunk.metadata.get("chunk_type") == "table_row" and not chunk.metadata.get("header_repeated"): errors.append({"chunk_id": chunk.chunk_id, "reason": "table_header_missing"})
    return {"chunk_count": len(chunks), "error_count": len(errors), "warning_count": len(warnings), "errors": errors, "warnings": warnings}


def chunk_conversion(conversion: DocumentConversion, *, size: int = 1200, overlap: int = 160, config: ChunkConfig | None = None) -> dict[str, Any]:
    cfg = config or ChunkConfig()
    if config is None and size != 1200: cfg.child_max_tokens = max(1, int(size))
    if config is None and overlap != 160: cfg.overlap_tokens = max(0, min(int(overlap), cfg.child_max_tokens - 1))
    if cfg.child_max_tokens <= 0 or not 0 <= cfg.overlap_tokens < cfg.child_max_tokens: raise ValueError("chunk token size/overlap 无效")
    chunks: list[ChunkRecord] = []
    for unit in build_structure_units(conversion, cfg):
        base = f"{conversion.document_id}:{unit.unit_id.rsplit(':', 1)[-1]}"; parent_text = "\n".join(_split_text(unit.text, cfg.parent_max_tokens)) or unit.text
        if cfg.include_parents: chunks.append(_record(conversion, unit, parent_text, chunk_id=f"{base}:parent", location=f"unit:{unit.unit_id}", parent_id=f"{base}:parent", level="parent", role="context_only"))
        for part, text in enumerate(_split_text(unit.text, cfg.child_max_tokens, cfg.overlap_tokens) or [unit.text]): chunks.append(_record(conversion, unit, text, chunk_id=f"{base}:child:{part}", location=f"unit:{unit.unit_id}:part:{part}", parent_id=f"{base}:parent", level="child", role="retrieval", part=part))
    chunks.extend(_table_chunks(conversion, cfg)); chunks.extend(_image_chunks(conversion)); chunks.extend(_visual_region_chunks(conversion)); validation = validate_chunks(chunks)
    quality_report = conversion.quality_report.to_dict()
    page_inventory = [page.to_dict() for page in conversion.pages]
    conversion_warnings = list(dict.fromkeys([*conversion.status.warnings, *conversion.quality_report.warnings]))
    metadata = dict(conversion.metadata)
    metadata.update({
        "source_path": conversion.source_path,
        "source_name": conversion.source_name,
        "extension": conversion.extension,
        "quality_score": conversion.quality_report.quality_score,
        "quality_report": quality_report,
        "conversion_warnings": conversion_warnings,
        "conversion_status": conversion.status.to_dict(),
        "page_count": conversion.quality_report.page_count or len(page_inventory),
    })
    return {"schema_version": CHUNK_SCHEMA_VERSION, "document_id": conversion.document_id, "source_path": conversion.source_path, "source_name": conversion.source_name, "source_sha256": conversion.source_sha256, "extension": conversion.extension, "quality_score": conversion.quality_report.quality_score, "quality_report": quality_report, "page_inventory": page_inventory, "conversion_warnings": conversion_warnings, "conversion_status": conversion.status.to_dict(), "metadata": metadata, "status": StageStatus(status="ready", stage_version=CHUNK_STAGE_VERSION, warnings=[f"chunk validation errors: {validation['error_count']}"] if validation["error_count"] else []).to_dict(), "chunk_config": {"child_max_tokens": cfg.child_max_tokens, "parent_max_tokens": cfg.parent_max_tokens, "overlap_tokens": cfg.overlap_tokens, "table_rows_per_child": cfg.table_rows_per_child}, "chunk_size": cfg.child_max_tokens, "chunk_overlap": cfg.overlap_tokens, "validation": validation, "image_references": _image_references(conversion), "table_diagnostics": _table_diagnostics(conversion), "chunks": [x.to_dict() for x in chunks]}


def chunk_json(input_path: str | Path, output_path: str | Path, *, size: int = 1200, overlap: int = 160, config: ChunkConfig | None = None) -> int:
    write_json(str(output_path), chunk_conversion(conversion_from_payload(read_json(str(input_path))), size=size, overlap=overlap, config=config)); return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chunk converted knowledge JSON"); parser.add_argument("input", type=Path); parser.add_argument("output", type=Path); parser.add_argument("--size", type=int, default=1200); parser.add_argument("--overlap", type=int, default=160); parser.add_argument("--child-max-tokens", type=int); parser.add_argument("--parent-max-tokens", type=int, default=DEFAULT_PARENT_TOKENS); parser.add_argument("--overlap-tokens", type=int); parser.add_argument("--table-rows-per-child", type=int, default=12); args = parser.parse_args(argv)
    config = ChunkConfig(args.child_max_tokens or DEFAULT_CHILD_TOKENS, args.parent_max_tokens, args.overlap_tokens if args.overlap_tokens is not None else DEFAULT_OVERLAP_TOKENS, max(1, args.table_rows_per_child)) if args.child_max_tokens is not None or args.overlap_tokens is not None else None
    return chunk_json(args.input, args.output, size=args.size, overlap=args.overlap, config=config)


if __name__ == "__main__": raise SystemExit(main())
