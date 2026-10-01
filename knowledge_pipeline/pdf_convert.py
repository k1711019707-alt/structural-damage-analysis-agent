"""Auditable PDF conversion with a Docling-first, PyMuPDF-safe-fallback pipeline.

The converter deliberately keeps extraction facts separate from later chunking and
retrieval. Docling is attempted by default for layout-aware projection, PyMuPDF
provides deterministic page facts and fallback extraction, and RapidOCR handles
scanned pages. A missing package or unavailable model must never make conversion
unusable.
"""
from __future__ import annotations

import argparse
import base64
import html as html_lib
import hashlib
import importlib.util
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .contracts import (
    BlockRecord,
    DocumentConversion,
    ImageRecord,
    PageRecord,
    QualityReport,
    StageStatus,
    TableRecord,
    write_json,
)


OCR_MIN_TEXT_CHARS = 12
VECTOR_TABLE_MIN_AREA_RATIO = 0.02
SCAN_NOISE_MAX_FOREGROUND_RATIO = 0.001
SCAN_NOISE_MAX_TEXT_BBOX_RATIO = 0.0005
SCAN_NOISE_MAX_TEXT_CHARS = 2
PDF_STAGE_VERSION = "pdf-convert.v3"
_SAFE_TABLE_TAGS = {"table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td"}


@dataclass
class PdfConversionOptions:
    """Runtime choices for the PDF stage; optional packages remain opt-in."""

    use_docling: bool = True
    use_camelot: bool = True
    use_pdfplumber: bool = True
    detect_table_candidates: bool = True
    render_scale: float = 2.0
    low_text_threshold: int = OCR_MIN_TEXT_CHARS
    strict_quality: bool = False
    docling_timeout_seconds: int = 120
    allow_docling_model_download: bool = True
    docling_device: str = "auto"
    docling_num_threads: int = 4
    docling_batch_size: int = 4
    review_artistic_headers: bool = True
    detect_scan_noise_pages: bool = True
    scan_noise_render_scale: float = 1.0
    header_review_scale: float = 3.0
    header_review_confidence: float = 0.88
    remote_blank_review: bool = False
    vision: "PdfVisionOptions | None" = None


@dataclass
class PdfVisionOptions:
    """Optional, bounded visual enhancement. No network call is made by default."""

    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-5.5"
    timeout_seconds: float = 60.0
    retries: int = 1
    max_input_pixels: int = 2_000_000
    adapter: object | None = None
    blank_page_review: bool = False

    @classmethod
    def from_gui_settings(cls, *, enabled: bool = False, adapter: object | None = None, blank_page_review: bool = False) -> "PdfVisionOptions":
        """Load URL/key from the existing user settings without persisting secrets."""
        try:
            from runtime.app_paths import user_config_path
            from runtime.settings_store import SettingsStore

            settings_path = user_config_path("gui_settings.json")
            legacy_path = user_config_path("gui_api_config.json")
            settings = SettingsStore(settings_path, legacy_path).load()
            return cls(enabled=enabled, base_url=str(settings.api.responses_url or ""), api_key=str(settings.api.responses_key or ""), model="gpt-5.5", adapter=adapter, blank_page_review=blank_page_review)
        except Exception:
            return cls(enabled=enabled, model="gpt-5.5", adapter=adapter, blank_page_review=blank_page_review)


class OpenAIResponsesVisionAdapter:
    """Small OpenAI-compatible Responses adapter used only when explicitly enabled."""

    def __init__(self, *, base_url: str, api_key: str, model: str = "gpt-5.5", timeout: float = 60.0, retries: int = 1) -> None:
        if not api_key:
            raise ValueError("视觉 Responses API key 为空")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("视觉增强需要 openai 依赖") from exc
        self.client = OpenAI(api_key=api_key, base_url=_normalize_responses_url(base_url), timeout=float(timeout), max_retries=0)
        self.model = model
        self.retries = max(0, int(retries))

    def analyze_page(self, *, image: Any, page_number: int, page_type: str, regions: list[dict[str, Any]], model: str, timeout: float) -> dict[str, Any]:
        import io

        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("视觉增强需要 Pillow 依赖") from exc
        image_obj = Image.fromarray(image) if not isinstance(image, Image.Image) else image
        image_obj.thumbnail((1800, 1800))
        buffer = io.BytesIO()
        image_obj.save(buffer, format="PNG", optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        prompt = {
            "page_number": page_number,
            "page_type": page_type,
            "regions": regions,
            "instruction": "仅输出 JSON object。逐区域区分图片可见文字 ocr_text 与视觉语义 vision_summary；不要臆造标准条文、数值或单位。",
        }
        response = self.client.responses.create(model=model or self.model, input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(prompt, ensure_ascii=False)}, {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"}]}], timeout=float(timeout))
        text = str(getattr(response, "output_text", "") or "").strip()
        if not text:
            output = getattr(response, "output", None) or []
            text = "".join(str(getattr(item, "text", "") or "") for item in output).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        return json.loads(text)

    def classify_blank_page(self, *, image: Any, page_number: int, model: str, timeout: float) -> dict[str, Any]:
        import io

        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("空白页视觉判定需要 Pillow 依赖") from exc
        image_obj = Image.fromarray(image) if not isinstance(image, Image.Image) else image
        image_obj.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        image_obj.save(buffer, format="PNG", optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "classification": {"type": "string", "enum": ["blank", "non_blank", "uncertain"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "has_readable_text": {"type": "boolean"},
                "has_graphics": {"type": "boolean"},
                "has_table": {"type": "boolean"},
                "has_stamp_or_annotation": {"type": "boolean"},
                "reason": {"type": "string", "maxLength": 500},
            },
            "required": ["classification", "confidence", "has_readable_text", "has_graphics", "has_table", "has_stamp_or_annotation", "reason"],
        }
        prompt = (
            "判断这张 PDF 页面是否为真正的空白页。若存在模糊文字、浅色内容、表格、图片、印章或无法确认的内容，返回 uncertain；"
            "不要根据页码或文件名猜测，只返回符合给定 JSON schema 的对象。"
        )
        response = self.client.responses.create(
            model=model or self.model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"}]}],
            text={"format": {"type": "json_schema", "name": "blank_page_decision", "schema": schema, "strict": True}},
            timeout=float(timeout),
        )
        text = str(getattr(response, "output_text", "") or "").strip()
        if not text:
            output = getattr(response, "output", None) or []
            text = "".join(str(getattr(item, "text", "") or "") for item in output).strip()
        return json.loads(text)


def _normalize_responses_url(value: str | None) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return "https://api.openai.com/v1"
    raw = re.sub(r"/v1/(responses|chat/completions)$", "/v1", raw, flags=re.I)
    return raw if re.search(r"/v1$", raw, flags=re.I) else f"{raw}/v1"


def _escape_cell(value: Any) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def rows_to_html(rows: Iterable[Iterable[Any]], *, caption: str = "") -> str:
    """Create safe, deterministic table HTML from a rectangular or ragged grid."""
    normalized = [[str(cell or "") for cell in row] for row in rows if row is not None]
    if not normalized:
        return ""
    width = max(len(row) for row in normalized)
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    head = normalized[0]
    body = normalized[1:]
    parts = ["<table>"]
    if caption:
        parts.append(f"<caption>{html_lib.escape(str(caption), quote=True)}</caption>")
    parts.append("<thead><tr>" + "".join(f"<th>{_escape_cell(cell)}</th>" for cell in head) + "</tr></thead>")
    if body:
        parts.append("<tbody>")
        for row in body:
            parts.append("<tr>" + "".join(f"<td>{_escape_cell(cell)}</td>" for cell in row) + "</tr>")
        parts.append("</tbody>")
    parts.append("</table>")
    return "".join(parts)


def normalize_table_html(value: str) -> str:
    """Normalize and reject unsafe table markup; return empty for invalid input."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        from html.parser import HTMLParser

        class _Parser(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.parts: list[str] = []
                self.invalid = False

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                tag = tag.lower()
                if tag not in _SAFE_TABLE_TAGS or attrs:
                    self.invalid = True
                    return
                self.parts.append(f"<{tag}>")

            def handle_endtag(self, tag: str) -> None:
                tag = tag.lower()
                if tag not in _SAFE_TABLE_TAGS:
                    self.invalid = True
                    return
                self.parts.append(f"</{tag}>")

            def handle_data(self, data: str) -> None:
                self.parts.append(html_lib.escape(data, quote=True))

            def handle_comment(self, _data: str) -> None:
                self.invalid = True

        parser = _Parser()
        parser.feed(raw)
        parser.close()
        if parser.invalid or not parser.parts or "<table>" not in parser.parts:
            return ""
        return "".join(parser.parts)
    except Exception:
        return ""


def table_html(table: TableRecord) -> str:
    """Return the canonical, validated HTML table body."""
    return normalize_table_html(table.html)


def _ensure_project_imports() -> None:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _document_id(path: Path, digest: str) -> str:
    return hashlib.sha256(f"{path.resolve()}:{digest}".encode()).hexdigest()[:20]


def _usable(text: str, threshold: int = OCR_MIN_TEXT_CHARS) -> bool:
    value = str(text)
    semantic = len(re.findall(r"[\w\u4e00-\u9fff]", value))
    # Some PDFs expose text with a non-Unicode embedded font.  It is still
    # safer to retain that native layer than to classify a populated page as
    # blank and discard it when OCR happens to return nothing.
    visible = len(re.findall(r"\S", value))
    return max(semantic, visible) >= max(1, threshold)


def _text_char_count(text: str) -> int:
    value = str(text)
    semantic = len(re.findall(r"[\w\u4e00-\u9fff]", value))
    return max(semantic, len(re.findall(r"\S", value)))


def _encoding_anomaly(text: str) -> bool:
    value = str(text)
    return bool(value) and (value.count("�") + value.count("��")) >= max(2, len(value) // 8)


def _optional_backend_status(options: PdfConversionOptions) -> dict[str, str]:
    requested = {
        "docling": options.use_docling,
        "camelot": options.use_camelot,
        "pdfplumber": options.use_pdfplumber,
    }
    result: dict[str, str] = {}
    for name, enabled in requested.items():
        if not enabled:
            result[name] = "disabled"
        elif importlib.util.find_spec(name) is None:
            result[name] = "missing"
        else:
            result[name] = "available"
    result["pymupdf"] = "available" if importlib.util.find_spec("pymupdf") else "missing"
    result["rapidocr"] = "available" if importlib.util.find_spec("rapidocr") else "missing"
    return result


def _page_text_blocks(page: Any) -> tuple[list[BlockRecord], str, int, list[BlockRecord], str, int]:
    """Separate visible native text from invisible OCR text layers."""
    try:
        payload = page.get_text("dict", sort=True)
    except TypeError:
        payload = page.get_text("dict")
    blocks: list[BlockRecord] = []
    raw_parts: list[str] = []
    hidden_blocks: list[BlockRecord] = []
    hidden_parts: list[str] = []
    for item_index, item in enumerate(payload.get("blocks", []) or []):
        if item.get("type") != 0:
            continue
        visible_lines: list[str] = []
        hidden_lines: list[str] = []
        for line in item.get("lines", []) or []:
            visible_spans: list[str] = []
            hidden_spans: list[str] = []
            for span in line.get("spans", []) or []:
                text = str(span.get("text", ""))
                if int(span.get("alpha", 255) or 0) <= 0:
                    hidden_spans.append(text)
                else:
                    visible_spans.append(text)
            visible_text = "".join(visible_spans).strip()
            hidden_text = "".join(hidden_spans).strip()
            if visible_text:
                visible_lines.append(visible_text)
            if hidden_text:
                hidden_lines.append(hidden_text)
        bbox = item.get("bbox")
        visible_text = "\n".join(visible_lines).strip()
        hidden_text = "\n".join(hidden_lines).strip()
        if visible_text:
            raw_parts.append(visible_text)
            blocks.append(
                BlockRecord(
                    block_id="",
                    location="",
                    text=visible_text,
                    block_type="text",
                    extraction_method="native:pymupdf",
                    bbox=[float(value) for value in bbox] if bbox and len(bbox) == 4 else None,
                    metadata={"text_raw": visible_text, "text_display": _display_text(visible_text), "text_search": _search_text(visible_text), "encoding_anomaly": _encoding_anomaly(visible_text)},
                )
            )
        if hidden_text:
            hidden_parts.append(hidden_text)
            hidden_blocks.append(
                BlockRecord(
                    block_id="",
                    location="",
                    text=hidden_text,
                    block_type="ocr_text_layer",
                    extraction_method="ocr:hidden-pdf-text",
                    bbox=[float(value) for value in bbox] if bbox and len(bbox) == 4 else None,
                    metadata={"text_raw": hidden_text, "text_display": _display_text(hidden_text), "text_search": _search_text(hidden_text), "hidden_text_layer": True, "encoding_anomaly": _encoding_anomaly(hidden_text)},
                )
            )
    raw = "\n".join(raw_parts).strip()
    hidden_raw = "\n".join(hidden_parts).strip()
    return blocks, raw, _text_char_count(raw), hidden_blocks, hidden_raw, _text_char_count(hidden_raw)


def _display_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", str(text)).strip()


def _search_text(text: str) -> str:
    # Preserve CJK and words while eliminating layout-only line breaks.
    return re.sub(r"\s+", " ", str(text)).strip()


def _page_images(page: Any, document_id: str, page_number: int) -> list[ImageRecord]:
    records: list[ImageRecord] = []
    try:
        images = page.get_images(full=True) or []
    except Exception:
        images = []
    for index, image in enumerate(images):
        xref = int(image[0]) if image else 0
        bbox = None
        try:
            rects = page.get_image_rects(xref) if xref else []
            if rects:
                rect = rects[0]
                bbox = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
        except Exception:
            bbox = None
        records.append(
            ImageRecord(
                image_id=f"{document_id}:page:{page_number}:image:{index}",
                page_number=page_number,
                bbox=bbox,
                width=int(image[2]) if len(image) > 2 else 0,
                height=int(image[3]) if len(image) > 3 else 0,
                image_hash=f"xref:{xref}" if xref else "",
                extraction_method="embedded-image",
            )
        )
    return records


def _preflight_page(page: Any, document_id: str, page_number: int, threshold: int) -> dict[str, Any]:
    native_blocks, native_text, native_chars, hidden_blocks, hidden_text, hidden_text_chars = _page_text_blocks(page)
    images = _page_images(page, document_id, page_number)
    try:
        drawings = page.get_drawings() or []
    except Exception:
        drawings = []
    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    image_area_ratio = _page_image_area_ratio(page)
    text_density = round(native_chars / page_area, 6)
    layout_complexity = _page_layout_complexity(text_blocks=len(native_blocks), drawing_count=len(drawings), image_count=len(images), image_area_ratio=image_area_ratio, page_area=page_area)
    vector_table_regions = _detect_vector_table_regions(page, drawings=drawings)
    has_table = bool(vector_table_regions)
    provisional, reason = _classify_routing(native_chars=native_chars, ocr_chars=0, image_count=len(images), drawing_count=len(drawings), image_area_ratio=image_area_ratio, layout_complexity=layout_complexity, has_table=has_table)
    if not _usable(native_text, threshold) and (images or image_area_ratio > 0.0 or hidden_text_chars > 0):
        provisional, reason = "scanned", "原生文字不足且页面包含图像内容"
    return {
        "native_blocks": native_blocks,
        "native_text": native_text,
        "native_chars": native_chars,
        "native_block_count": len(native_blocks),
        "hidden_blocks": hidden_blocks,
        "hidden_text": hidden_text,
        "hidden_text_chars": hidden_text_chars,
        "images": images,
        "drawing_count": len(drawings),
        "vector_table_regions": vector_table_regions,
        "image_area_ratio": image_area_ratio,
        "text_density": text_density,
        "encoding_anomaly": _encoding_anomaly(native_text),
        "layout_complexity": layout_complexity,
        "has_table": has_table,
        "routing_type": provisional,
        "classification_reason": reason,
    }


def _page_image_area_ratio(page: Any) -> float:
    try:
        page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
        total = 0.0
        try:
            image_info = page.get_image_info(hashes=False, xrefs=True) or []
        except TypeError:
            image_info = page.get_image_info() or []
        for item in image_info:
            bbox = item.get("bbox") or []
            if len(bbox) == 4:
                total += max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))
        return round(min(1.0, total / page_area), 4)
    except Exception:
        return 0.0


def _page_layout_complexity(*, text_blocks: int, drawing_count: int, image_count: int, image_area_ratio: float, page_area: float) -> float:
    density = (float(drawing_count) + float(image_count) * 2.0 + float(text_blocks)) / max(1.0, page_area / 10000.0)
    score = min(1.0, 0.35 * min(1.0, image_area_ratio) + 0.35 * min(1.0, drawing_count / 40.0) + 0.30 * min(1.0, density / 80.0))
    return round(score, 4)


def _classify_routing(*, native_chars: int, ocr_chars: int, image_count: int, drawing_count: int, image_area_ratio: float, layout_complexity: float, has_table: bool) -> tuple[str, str]:
    if native_chars == 0 and ocr_chars == 0 and image_count == 0 and drawing_count == 0:
        return "blank_or_unreadable", "无原生文字、OCR、图片或绘图"
    if ocr_chars and not native_chars:
        return "scanned", "原生文字不足且 OCR 产生文字"
    if has_table and native_chars:
        return "native_table", "原生文字与密集矢量/表格候选共存"
    if image_area_ratio >= 0.55 or (image_count > 0 and layout_complexity >= 0.65):
        return "figure_page", "图片占比或视觉复杂度较高"
    if native_chars and ocr_chars:
        return "mixed", "原生文字与 OCR 文字共存"
    if layout_complexity >= 0.55:
        return "complex_layout", "版面复杂度达到区域增强阈值"
    if native_chars:
        return "native_text", "原生文字充足且版面复杂度较低"
    return "mixed", "存在视觉内容但无充分原生文字"


def _classify_page(native_chars: int, ocr_chars: int, image_count: int, drawing_count: int) -> str:
    if native_chars == 0 and ocr_chars == 0 and image_count == 0 and drawing_count == 0:
        return "blank_or_unreadable"
    if native_chars and ocr_chars:
        return "mixed"
    if ocr_chars and not native_chars:
        return "scanned"
    if native_chars:
        return "native"
    return "mixed"


def _point_xy(value: Any) -> tuple[float, float] | None:
    try:
        return float(value.x), float(value.y)
    except Exception:
        try:
            return float(value[0]), float(value[1])
        except Exception:
            return None


def _detect_vector_table_regions(page: Any, *, drawings: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return local axis-aligned grid regions instead of whole-page drawing guesses."""
    try:
        drawing_items = drawings if drawings is not None else list(page.get_drawings() or [])
        page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    except Exception:
        return []
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    tolerance = 1.5

    def add_line(start: Any, end: Any) -> None:
        left = _point_xy(start)
        right = _point_xy(end)
        if left is None or right is None:
            return
        x0, y0 = left
        x1, y1 = right
        if abs(y1 - y0) <= tolerance and abs(x1 - x0) >= 8.0:
            horizontal.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2.0))
        elif abs(x1 - x0) <= tolerance and abs(y1 - y0) >= 8.0:
            vertical.append(((x0 + x1) / 2.0, min(y0, y1), max(y0, y1)))

    for drawing in drawing_items:
        for item in drawing.get("items", []) or []:
            if not item:
                continue
            if item[0] == "l" and len(item) >= 3:
                add_line(item[1], item[2])
            elif item[0] == "re" and len(item) >= 2:
                rect = item[1]
                try:
                    add_line((rect.x0, rect.y0), (rect.x1, rect.y0))
                    add_line((rect.x1, rect.y0), (rect.x1, rect.y1))
                    add_line((rect.x1, rect.y1), (rect.x0, rect.y1))
                    add_line((rect.x0, rect.y1), (rect.x0, rect.y0))
                except Exception:
                    continue

    horizontal = list(dict.fromkeys(tuple(round(value, 2) for value in line) for line in horizontal))
    vertical = list(dict.fromkeys(tuple(round(value, 2) for value in line) for line in vertical))
    edges: list[tuple[int, int]] = []
    for horizontal_index, (x0, x1, y) in enumerate(horizontal):
        for vertical_index, (x, y0, y1) in enumerate(vertical):
            if x0 - tolerance <= x <= x1 + tolerance and y0 - tolerance <= y <= y1 + tolerance:
                edges.append((horizontal_index, vertical_index))
    if len(edges) < 4:
        return []

    adjacency_h: dict[int, set[int]] = {}
    adjacency_v: dict[int, set[int]] = {}
    for horizontal_index, vertical_index in edges:
        adjacency_h.setdefault(horizontal_index, set()).add(vertical_index)
        adjacency_v.setdefault(vertical_index, set()).add(horizontal_index)
    regions: list[dict[str, Any]] = []
    visited_h: set[int] = set()
    visited_v: set[int] = set()
    for start in sorted(adjacency_h):
        if start in visited_h:
            continue
        component_h: set[int] = set()
        component_v: set[int] = set()
        pending_h = [start]
        while pending_h:
            horizontal_index = pending_h.pop()
            if horizontal_index in component_h:
                continue
            component_h.add(horizontal_index)
            for vertical_index in adjacency_h.get(horizontal_index, set()):
                if vertical_index not in component_v:
                    component_v.add(vertical_index)
                    pending_h.extend(adjacency_v.get(vertical_index, set()) - component_h)
        visited_h.update(component_h)
        visited_v.update(component_v)
        component_edges = [(h, v) for h, v in edges if h in component_h and v in component_v]
        if len(component_h) < 2 or len(component_v) < 2 or len(component_edges) < 4:
            continue
        x_values = [horizontal[index][0] for index in component_h] + [horizontal[index][1] for index in component_h] + [vertical[index][0] for index in component_v]
        y_values = [horizontal[index][2] for index in component_h] + [vertical[index][1] for index in component_v] + [vertical[index][2] for index in component_v]
        bbox = [min(x_values), min(y_values), max(x_values), max(y_values)]
        area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
        area_ratio = area / page_area
        if area_ratio < VECTOR_TABLE_MIN_AREA_RATIO or area_ratio >= 0.95:
            continue
        regions.append({
            "bbox": [round(float(value), 2) for value in bbox],
            "horizontal_line_count": len(component_h),
            "vertical_line_count": len(component_v),
            "intersection_count": len(component_edges),
            "area_ratio": round(area_ratio, 4),
        })
    return sorted(regions, key=lambda item: tuple(item["bbox"]))


def _table_candidates(
    page: Any,
    *,
    document_id: str,
    page_number: int,
    drawing_count: int,
    native_chars: int = 0,
    image_area_ratio: float = 0.0,
    regions: list[dict[str, Any]] | None = None,
    enabled: bool = True,
) -> list[TableRecord]:
    if not enabled or drawing_count < 4:
        return []
    if native_chars < OCR_MIN_TEXT_CHARS and image_area_ratio >= 0.70:
        return []
    detected = regions if regions is not None else _detect_vector_table_regions(page)
    return [
        TableRecord(
            table_id=f"{document_id}:page:{page_number}:table:candidate:{index}",
            page_number=page_number,
            bbox=[float(value) for value in region["bbox"]],
            extraction_method="candidate:vector-drawings",
            metadata={
                "drawing_count": drawing_count,
                "reason": "local_vector_grid",
                "horizontal_line_count": int(region["horizontal_line_count"]),
                "vertical_line_count": int(region["vertical_line_count"]),
                "intersection_count": int(region["intersection_count"]),
                "area_ratio": float(region.get("area_ratio", 0.0)),
            },
        )
        for index, region in enumerate(detected)
    ]


def _render_page(path: Path, page_number: int, scale: float) -> Any:
    import numpy as np
    import pymupdf

    with pymupdf.open(str(path)) as document:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"PDF 页码超出范围：{page_number}")
        pixmap = document[page_number - 1].get_pixmap(
            matrix=pymupdf.Matrix(max(0.5, scale), max(0.5, scale)), alpha=False
        )
        return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)


def _scan_noise_review(
    image: Any,
    *,
    page_blocks: list[BlockRecord],
    page_width: float,
    page_height: float,
    has_table: bool,
) -> dict[str, Any]:
    """Conservatively identify a near-white scan containing only micro-noise.

    This is deliberately stricter than generic blank-page detection: sparse but
    meaningful titles, formulas, stamps, and drawings must remain available to
    OCR and review. The detector only accepts negligible foreground plus absent
    or page-relative micro text boxes.
    """
    try:
        import numpy as np

        values = np.asarray(image)
        if values.ndim == 3:
            values = values[..., :3].astype(np.float32).mean(axis=2)
        elif values.ndim == 2:
            values = values.astype(np.float32)
        else:
            raise ValueError("unsupported rendered page shape")
        if values.size == 0:
            raise ValueError("empty rendered page")
        foreground = values < 245.0
        foreground_pixels = int(foreground.sum())
        pixel_count = int(foreground.size)
        foreground_ratio = foreground_pixels / max(1, pixel_count)
    except Exception as exc:
        return {
            "classification": "uncertain",
            "confidence": 0.0,
            "reason": f"本地近空白检测失败：{type(exc).__name__}",
            "foreground_ratio": 1.0,
            "foreground_pixels": 0,
            "significant_component_count": 0,
            "noise_component_count": 0,
            "suppressed_text": [],
        }

    page_area = max(1.0, float(page_width) * float(page_height))
    visible_blocks = [block for block in page_blocks if str(block.text).strip()]
    total_text_chars = sum(_text_char_count(block.text) for block in visible_blocks)
    bbox_areas: list[float] = []
    for block in visible_blocks:
        bbox = block.bbox or []
        if len(bbox) == 4:
            bbox_areas.append(max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1])))
    text_bbox_ratio = sum(bbox_areas) / page_area
    micro_text_only = bool(
        total_text_chars <= SCAN_NOISE_MAX_TEXT_CHARS
        and (not visible_blocks or (len(bbox_areas) == len(visible_blocks) and text_bbox_ratio <= SCAN_NOISE_MAX_TEXT_BBOX_RATIO))
    )

    significant_components = 0
    noise_components = 0
    largest_component_pixels = 0
    largest_component_bbox_ratio = 0.0
    if foreground_ratio <= SCAN_NOISE_MAX_FOREGROUND_RATIO and foreground_pixels:
        remaining = {tuple(int(value) for value in point) for point in np.argwhere(foreground)}
        significant_min_pixels = max(24, int(round(pixel_count * 0.00008)))
        height, width = foreground.shape
        while remaining:
            start = remaining.pop()
            stack = [start]
            area = 0
            min_y = max_y = start[0]
            min_x = max_x = start[1]
            while stack:
                y, x = stack.pop()
                area += 1
                min_y, max_y = min(min_y, y), max(max_y, y)
                min_x, max_x = min(min_x, x), max(max_x, x)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if not (dx or dy):
                            continue
                        point = (y + dy, x + dx)
                        if point in remaining:
                            remaining.remove(point)
                            stack.append(point)
            component_width = max_x - min_x + 1
            component_height = max_y - min_y + 1
            component_bbox_ratio = component_width * component_height / max(1, pixel_count)
            largest_component_pixels = max(largest_component_pixels, area)
            largest_component_bbox_ratio = max(largest_component_bbox_ratio, component_bbox_ratio)
            if area >= significant_min_pixels or component_width >= max(8, int(width * 0.02)) or component_height >= max(8, int(height * 0.02)):
                significant_components += 1
            else:
                noise_components += 1

    scan_noise_only = bool(
        not has_table
        and foreground_ratio <= SCAN_NOISE_MAX_FOREGROUND_RATIO
        and significant_components == 0
        and micro_text_only
    )
    return {
        "classification": "scan_noise_only" if scan_noise_only else "non_blank",
        "confidence": 0.99 if scan_noise_only else 0.95,
        "reason": (
            "页面前景像素占比极低，仅包含微小孤立区域且没有有效文字或表格结构"
            if scan_noise_only
            else "页面包含超过近空白阈值的文字或视觉区域"
        ),
        "foreground_ratio": round(float(foreground_ratio), 8),
        "foreground_pixels": foreground_pixels,
        "text_bbox_area_ratio": round(float(text_bbox_ratio), 8),
        "significant_component_count": significant_components,
        "noise_component_count": noise_components,
        "largest_component_pixels": largest_component_pixels,
        "largest_component_bbox_ratio": round(float(largest_component_bbox_ratio), 8),
        "suppressed_text": [str(block.text).strip() for block in visible_blocks] if scan_noise_only else [],
    }


def _ocr_adapter(ocr: object | None) -> object:
    if ocr is not None:
        return ocr
    _ensure_project_imports()
    from runtime.knowledge_base import LocalOcrAdapter

    return LocalOcrAdapter()


def _docling_cell_matching_enabled(preflights: dict[int, dict[str, Any]], *, scan_threshold: float = 0.80) -> bool:
    if not preflights:
        return True
    scanned = sum(str(item.get("routing_type")) == "scanned" for item in preflights.values())
    if scanned / len(preflights) >= float(scan_threshold):
        return False
    drawing_counts = sorted(max(0, int(item.get("drawing_count", 0))) for item in preflights.values())
    high_geometry_ratio = sum(value >= 50 for value in drawing_counts) / len(drawing_counts)
    lower = drawing_counts[max(0, int(len(drawing_counts) * 0.10) - 1)]
    upper = drawing_counts[min(len(drawing_counts) - 1, int(len(drawing_counts) * 0.90))]
    repeated_scan_geometry = high_geometry_ratio >= float(scan_threshold) and upper - lower <= 10
    return not repeated_scan_geometry


def _try_docling(
    path: Path,
    *,
    document_id: str = "",
    allow_model_download: bool = False,
    cell_matching: bool = True,
    device: str = "auto",
    num_threads: int = 4,
    batch_size: int = 4,
) -> tuple[str, dict[int, list[BlockRecord]], list[TableRecord], str | None]:
    if importlib.util.find_spec("docling") is None:
        return "", {}, [], "Docling 未安装，已使用 PyMuPDF 基础路径"
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        previous_offline = os.environ.get("HF_HUB_OFFLINE")
        if not allow_model_download:
            os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.accelerator_options = AcceleratorOptions(
                num_threads=max(1, int(num_threads)),
                device=str(device or "auto"),
            )
            safe_batch_size = max(1, int(batch_size))
            pipeline_options.ocr_batch_size = safe_batch_size
            pipeline_options.layout_batch_size = safe_batch_size
            pipeline_options.table_batch_size = safe_batch_size
            pipeline_options.queue_max_size = max(2, safe_batch_size * 2)
            pipeline_options.table_structure_options.do_cell_matching = bool(cell_matching)
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )
            result = converter.convert(str(path))
        finally:
            if previous_offline is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = previous_offline
        document = getattr(result, "document", result)
        projection_markdown = document.export_to_markdown() if hasattr(document, "export_to_markdown") else ""
        payload = document.export_to_dict() if hasattr(document, "export_to_dict") else {}
        page_blocks: dict[int, list[BlockRecord]] = {}
        for item_index, item in enumerate(payload.get("texts", []) or []):
            text = str(item.get("text") or item.get("orig") or "").strip()
            if not text:
                continue
            provenance = item.get("prov") or []
            prov = provenance[0] if provenance else {}
            page_number = int(prov.get("page_no") or 0)
            bbox = prov.get("bbox") or {}
            coords = [bbox.get(key) for key in ("l", "b", "r", "t")]
            block = BlockRecord(
                block_id="",
                location="",
                text=text,
                block_type="layout_text",
                page_number=page_number or None,
                extraction_method="layout:docling",
                bbox=[float(value) for value in coords] if all(value is not None for value in coords) else None,
                metadata={"text_raw": str(item.get("orig") or text), "text_display": _display_text(text), "text_search": _search_text(text), "docling_label": str(item.get("label") or "")},
            )
            page_blocks.setdefault(page_number, []).append(block)
        structured_tables: list[TableRecord] = []
        for table_index, item in enumerate(payload.get("tables", []) or []):
            provenance = item.get("prov") or []
            prov = provenance[0] if provenance else {}
            page_number = int(prov.get("page_no") or 0)
            bbox = prov.get("bbox") or {}
            coords = [bbox.get(key) for key in ("l", "b", "r", "t")]
            rows: list[list[str]] = []
            data = item.get("data") or item.get("table_data") or {}
            raw_grid = data.get("grid") if isinstance(data, dict) else None
            if isinstance(raw_grid, list):
                for row in raw_grid:
                    if isinstance(row, list):
                        rows.append([str(cell.get("text", "") if isinstance(cell, dict) else cell or "").strip() for cell in row])
            if not rows:
                table_markdown = str(item.get("text") or "").strip()
            else:
                table_markdown = "\n".join("| " + " | ".join(row) + " |" for row in rows)
            canonical_html = rows_to_html(rows) if rows else ""
            structured_tables.append(TableRecord(table_id=f"{document_id or 'docling'}:table:docling:{table_index}", page_number=page_number, bbox=[float(value) for value in coords] if all(value is not None for value in coords) else None, html=canonical_html, extraction_method="table:docling", confidence=None, needs_review=not bool(canonical_html), metadata={"docling_label": str(item.get("label") or ""), "row_count": len(rows), "column_count": max((len(row) for row in rows), default=0), "html_valid": bool(canonical_html), "quality_flags": [] if canonical_html else ["table_without_cells"] }))
        return str(projection_markdown or "").strip(), page_blocks, structured_tables, None
    except Exception as exc:
        return "", {}, [], f"Docling 解析失败，已回退 PyMuPDF：{type(exc).__name__}: {exc}"


def _try_optional_tables(path: Path, options: PdfConversionOptions, document_id: str, *, page_numbers: set[int] | None = None) -> tuple[list[TableRecord], list[str]]:
    """Extract only confidently tabular rows from explicitly enabled backends."""
    records: list[TableRecord] = []
    warnings: list[str] = []
    if page_numbers is not None and not page_numbers:
        return records, warnings
    if options.use_camelot and importlib.util.find_spec("camelot") is not None:
        try:
            import camelot

            pages_arg = ",".join(str(page) for page in sorted(page_numbers or []))
            if pages_arg:
                for table_index, table in enumerate(camelot.read_pdf(str(path), pages=pages_arg, flavor="lattice")):
                    rows = [[str(cell or "").strip() for cell in row] for row in table.df.values.tolist()]
                    if not rows:
                        continue
                    page_number = int(getattr(table, "page", 0) or 0)
                    canonical_html = rows_to_html(rows)
                    records.append(TableRecord(table_id=f"{document_id}:table:camelot:{table_index}", page_number=page_number, html=canonical_html, extraction_method="table:camelot", confidence=None, needs_review=True, metadata={"row_count": len(rows), "column_count": max((len(row) for row in rows), default=0), "html_valid": bool(canonical_html), "quality_flags": [] if canonical_html else ["invalid_html"]}))
        except Exception as exc:
            warnings.append(f"Camelot 表格解析失败，保留候选检测：{type(exc).__name__}: {exc}")
    if options.use_pdfplumber and importlib.util.find_spec("pdfplumber") is not None:
        try:
            import pdfplumber

            with pdfplumber.open(str(path)) as document:
                for page_number, page in enumerate(document.pages, start=1):
                    if page_numbers is not None and page_number not in page_numbers:
                        continue
                    for table_index, rows in enumerate(page.extract_tables() or []):
                        clean_rows = [[str(cell or "").strip() for cell in row] for row in rows if row]
                        if clean_rows:
                            canonical_html = rows_to_html(clean_rows)
                            records.append(TableRecord(table_id=f"{document_id}:table:pdfplumber:{page_number}:{table_index}", page_number=page_number, html=canonical_html, extraction_method="table:pdfplumber", confidence=None, needs_review=True, metadata={"row_count": len(clean_rows), "column_count": max((len(row) for row in clean_rows), default=0), "html_valid": bool(canonical_html), "quality_flags": [] if canonical_html else ["invalid_html"]}))
        except Exception as exc:
            warnings.append(f"pdfplumber 表格解析失败，保留候选检测：{type(exc).__name__}: {exc}")
    return records, warnings


def _structured_ocr_result(adapter: object, image: Any) -> list[dict[str, Any]]:
    """Normalize common RapidOCR result shapes without inventing confidence."""
    engine = getattr(adapter, "_get_engine", None)
    if engine is None:
        return []
    try:
        output = engine()(image)
    except Exception:
        return []

    def field(names: tuple[str, ...]) -> Any:
        for name in names:
            value = output.get(name) if isinstance(output, dict) else getattr(output, name, None)
            if value is not None:
                return value
        return None

    def sequence(value: Any) -> list[Any]:
        if value is None:
            return []
        if hasattr(value, "tolist"):
            try:
                value = value.tolist()
            except Exception:
                pass
        if isinstance(value, (list, tuple)):
            return list(value)
        try:
            return list(value)
        except (TypeError, ValueError):
            return []

    boxes = field(("boxes", "polys"))
    texts = field(("txts", "texts"))
    scores = field(("scores", "scores_list"))
    if texts is None:
        rows = sequence(output)
        # Older RapidOCR releases may return ``(result_rows, elapsed)``.
        if len(rows) == 2 and isinstance(rows[0], (list, tuple)) and not isinstance(rows[1], (list, tuple, dict)):
            rows = sequence(rows[0])
        normalized_rows = [sequence(row) for row in rows]
        normalized_rows = [row for row in normalized_rows if len(row) >= 2]
        if normalized_rows:
            boxes = [row[0] for row in normalized_rows]
            texts = [row[1] for row in normalized_rows]
            scores = [row[2] if len(row) > 2 else None for row in normalized_rows]

    text_values = sequence(texts)
    if not text_values:
        return []
    box_values = sequence(boxes)
    score_values = sequence(scores)
    result: list[dict[str, Any]] = []
    for index, text in enumerate(text_values):
        value = str("" if text is None else text).strip()
        if not value:
            continue
        raw_box = box_values[index] if index < len(box_values) else None
        bbox = None
        if raw_box is not None:
            try:
                points = [[float(point[0]), float(point[1])] for point in raw_box]
                bbox = [min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points)]
            except Exception:
                bbox = None
        confidence = None
        if index < len(score_values):
            try:
                confidence = float(score_values[index])
            except Exception:
                confidence = None
        result.append({"text": value, "bbox": bbox, "confidence": confidence, "order": index})
    return result


def _bbox_iou(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != 4 or len(right) != 4:
        return 0.0
    x0 = max(float(left[0]), float(right[0]))
    y0 = max(float(left[1]), float(right[1]))
    x1 = min(float(left[2]), float(right[2]))
    y1 = min(float(left[3]), float(right[3]))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, float(left[2]) - float(left[0])) * max(0.0, float(left[3]) - float(left[1]))
    right_area = max(0.0, float(right[2]) - float(right[0])) * max(0.0, float(right[3]) - float(right[1]))
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _header_review_text_is_plausible(original: str, candidate: str) -> bool:
    original_value = _search_text(original)
    candidate_value = _search_text(candidate)
    if not candidate_value or not re.search(r"[\w\u4e00-\u9fff]", candidate_value):
        return False
    if not original_value:
        return True
    original_chars = _text_char_count(original_value)
    candidate_chars = _text_char_count(candidate_value)
    if candidate_chars <= 0:
        return False
    # Reject likely partial detections (for example a long English header
    # recognized as only its first few words) as well as implausible expansions.
    minimum_chars = max(1, int(original_chars * 0.65 + 0.5))
    maximum_chars = max(original_chars * 2, original_chars + 12)
    if candidate_chars < minimum_chars or candidate_chars > maximum_chars:
        return False
    return True


def _header_review_comparable_text(text: str) -> str:
    """Ignore layout spaces and width-only punctuation changes for review decisions."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", _search_text(text))).casefold()


def _review_artistic_headers(
    *,
    source: Path,
    page: Any,
    page_number: int,
    page_blocks: list[BlockRecord],
    adapter: object,
    options: PdfConversionOptions,
) -> tuple[dict[str, int], list[str]]:
    """Review Docling page-header blocks with one local OCR pass per page."""
    candidates = [
        block for block in page_blocks
        if block.metadata.get("docling_label") == "page_header" and block.bbox and len(block.bbox) == 4
    ]
    empty_stats = {"review_count": 0, "recovered_count": 0, "confirmed_count": 0, "needs_review_count": 0}
    if not options.review_artistic_headers or not candidates:
        return empty_stats, []
    stats = dict(empty_stats, review_count=len(candidates))
    warnings: list[str] = []
    try:
        rendered = _render_page(source, page_number, max(0.5, float(options.header_review_scale)))
        ocr_items = _structured_ocr_result(adapter, rendered)
    except Exception as exc:
        for block in candidates:
            block.metadata["header_review"] = {
                "status": "needs_review",
                "original_text": block.text,
                "ocr_text": "",
                "ocr_confidence": None,
                "source": "rapidocr:local-header-review",
                "reason": f"OCR failed: {type(exc).__name__}",
            }
        stats["needs_review_count"] = len(candidates)
        warnings.append(f"页眉艺术字局部复核失败：{type(exc).__name__}: {exc}")
        return stats, warnings
    try:
        image_height, image_width = int(rendered.shape[0]), int(rendered.shape[1])
        page_width, page_height = float(page.rect.width), float(page.rect.height)
    except Exception as exc:
        stats["needs_review_count"] = len(candidates)
        warnings.append(f"页眉艺术字坐标映射失败：{type(exc).__name__}: {exc}")
        return stats, warnings

    mapped_items: list[dict[str, Any]] = []
    for item in ocr_items:
        pixel_bbox = item.get("bbox")
        if not pixel_bbox or len(pixel_bbox) != 4:
            continue
        x0, y0, x1, y1 = [float(value) for value in pixel_bbox]
        pdf_x0 = max(0.0, min(page_width, x0 * page_width / max(1, image_width)))
        pdf_x1 = max(0.0, min(page_width, x1 * page_width / max(1, image_width)))
        top_y0 = max(0.0, min(page_height, y0 * page_height / max(1, image_height)))
        top_y1 = max(0.0, min(page_height, y1 * page_height / max(1, image_height)))
        mapped_items.append({**item, "pdf_bbox": [pdf_x0, page_height - top_y1, pdf_x1, page_height - top_y0]})

    used: set[int] = set()
    for block in candidates:
        original = str(block.text)
        best_index = -1
        best_score = 0.0
        for index, item in enumerate(mapped_items):
            if index in used:
                continue
            overlap = _bbox_iou(block.bbox, item.get("pdf_bbox"))
            if overlap > best_score:
                best_score, best_index = overlap, index
        matched = mapped_items[best_index] if best_index >= 0 and best_score >= 0.05 else None
        confidence = matched.get("confidence") if matched else None
        candidate_text = str(matched.get("text") or "") if matched else ""
        if matched is not None:
            used.add(best_index)
        metadata = {
            "status": "needs_review",
            "original_text": original,
            "ocr_text": candidate_text,
            "ocr_confidence": confidence,
            "bbox_iou": round(best_score, 4) if matched is not None else 0.0,
            "source": "rapidocr:local-header-review",
        }
        if matched is not None and confidence is not None and float(confidence) >= float(options.header_review_confidence) and _header_review_text_is_plausible(original, candidate_text):
            normalized_original = _header_review_comparable_text(original)
            normalized_candidate = _header_review_comparable_text(candidate_text)
            if normalized_original == normalized_candidate:
                metadata["status"] = "confirmed"
                stats["confirmed_count"] += 1
            else:
                metadata["status"] = "recovered"
                stats["recovered_count"] += 1
                block.text = candidate_text
                block.metadata["text_display"] = _display_text(candidate_text)
                block.metadata["text_search"] = _search_text(candidate_text)
        else:
            stats["needs_review_count"] += 1
        block.metadata["header_review"] = metadata
    return stats, warnings


def _quality_score(report: QualityReport) -> float:
    if report.page_count <= 0:
        return 0.0
    excluded_pages = {
        *[int(page) for page in report.intentional_blank_pages],
        *[int(page) for page in report.scan_noise_only_pages],
    }
    effective_pages = int(report.effective_content_pages) or max(0, int(report.page_count) - len(excluded_pages))
    score = 1.0
    if effective_pages:
        failed = {int(page) for page in report.failed_pages if int(page) not in excluded_pages}
        low_quality = {int(page) for page in report.low_quality_pages if int(page) not in excluded_pages} - failed
        score -= min(0.55, len(failed) / effective_pages * 0.55)
        score -= min(0.30, len(low_quality) / effective_pages * 0.30)
    table_count = max(0, int(report.table_metrics.get("table_count", 0)))
    if table_count:
        invalid_ratio = min(1.0, max(0, int(report.table_metrics.get("invalid_table_count", 0))) / table_count)
        review_ratio = min(1.0, max(0, int(report.table_metrics.get("needs_review_count", 0))) / table_count)
        score -= min(0.20, invalid_ratio * 0.20)
        score -= min(0.10, review_ratio * 0.10)
    header_review_count = max(0, int(report.header_review_metrics.get("review_count", 0)))
    if header_review_count:
        unresolved_ratio = min(1.0, max(0, int(report.header_review_metrics.get("needs_review_count", 0))) / header_review_count)
        score -= min(0.05, unresolved_ratio * 0.05)
    confidence_observations = max(0, int(report.text_metrics.get("ocr_confidence_observation_count", 0)))
    if confidence_observations:
        low_confidence_ratio = min(1.0, max(0.0, float(report.text_metrics.get("low_confidence_ocr_block_ratio", 0.0))))
        score -= min(0.10, low_confidence_ratio * 0.10)
    return round(max(0.0, score), 3)


def _finalize_quality_report(report: QualityReport) -> None:
    """Normalize page inventories before scoring or serializing diagnostics."""
    report.intentional_blank_pages = sorted({int(page) for page in report.intentional_blank_pages if int(page) > 0})
    report.scan_noise_only_pages = sorted({int(page) for page in report.scan_noise_only_pages if int(page) > 0})
    report.successfully_recovered_ocr_pages = sorted({int(page) for page in report.successfully_recovered_ocr_pages if int(page) > 0})
    intentional = {*report.intentional_blank_pages, *report.scan_noise_only_pages}
    report.effective_content_pages = max(0, int(report.page_count) - len(intentional))
    report.failed_pages = sorted({int(page) for page in report.failed_pages if int(page) > 0})
    report.failed_pages = [page for page in report.failed_pages if page not in intentional]
    report.low_quality_pages = sorted({int(page) for page in [*report.low_quality_pages, *report.failed_pages] if int(page) > 0 and int(page) not in intentional})
    report.successfully_recovered_ocr_pages = [page for page in report.successfully_recovered_ocr_pages if page not in intentional and page not in report.failed_pages]
    content_successes = max(0, report.effective_content_pages - len(report.failed_pages))
    report.page_metrics.update({
        "effective_content_pages": report.effective_content_pages,
        "intentional_blank_page_count": len(report.intentional_blank_pages),
        "scan_noise_only_page_count": len(report.scan_noise_only_pages),
        "successfully_recovered_ocr_page_count": len(report.successfully_recovered_ocr_pages),
        "failed_content_page_count": len(report.failed_pages),
        "low_quality_content_page_count": len(report.low_quality_pages),
        "content_page_success_ratio": round(content_successes / report.effective_content_pages, 4) if report.effective_content_pages else 1.0,
    })
    report.warnings = list(dict.fromkeys(str(item) for item in report.warnings if str(item).strip()))
    report.quality_score = _quality_score(report)
    report.needs_review = bool(
        report.low_quality_pages
        or report.failed_pages
        or int(report.table_metrics.get("invalid_table_count", 0)) > 0
        or int(report.table_metrics.get("needs_review_count", 0)) > 0
        or int(report.header_review_metrics.get("needs_review_count", 0)) > 0
    )


def _dedupe_tables(records: list[TableRecord]) -> list[TableRecord]:
    """Cluster by page/bbox and canonical HTML, retaining deterministic sources."""
    priority = {"table:docling": 0, "table:camelot": 1, "table:pdfplumber": 2, "vision:vlm": 3, "candidate:vector-drawings": 9}
    ordered = sorted(records, key=lambda item: (item.page_number or 10**9, priority.get(item.extraction_method, 5), item.table_id))
    result: list[TableRecord] = []
    for table in ordered:
        canonical = table_html(table)
        if canonical:
            table.html = canonical
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            table.metadata["normalized_html_hash"] = digest
            table.metadata["html_valid"] = True
        else:
            digest = ""
            table.metadata.setdefault("quality_flags", []).append("table_without_valid_html")
            table.metadata["html_valid"] = False
        duplicate: TableRecord | None = None
        for existing in result:
            if existing.page_number != table.page_number:
                continue
            same_hash = bool(digest and existing.metadata.get("normalized_html_hash") == digest)
            same_region = bool(table.bbox and existing.bbox and all(abs(float(a) - float(b)) <= 8.0 for a, b in zip(table.bbox, existing.bbox)))
            overlapping_empty_candidate = bool(
                table.extraction_method == "candidate:vector-drawings"
                and not canonical
                and existing.extraction_method != "candidate:vector-drawings"
                and _bbox_iou(table.bbox, existing.bbox) >= 0.30
            )
            if same_hash or (same_region and canonical and table_html(existing) == canonical) or overlapping_empty_candidate:
                duplicate = existing
                break
        if duplicate is None:
            result.append(table)
            continue
        duplicate.metadata.setdefault("alternative_sources", []).append({"table_id": table.table_id, "extraction_method": table.extraction_method, "normalized_html_hash": digest, "bbox": table.bbox})
        if canonical and table_html(duplicate) != canonical:
            duplicate.metadata.setdefault("quality_flags", []).append("table_source_conflict")
            duplicate.needs_review = True
    return result


def _redacted_error(exc: Exception, key: str = "") -> str:
    value = f"{type(exc).__name__}: {exc}"
    return value.replace(key, "<redacted>") if key else value


def _vision_payload(adapter: object, *, image: Any, page_number: int, page_type: str, regions: list[dict[str, Any]], options: PdfVisionOptions) -> dict[str, Any]:
    method = getattr(adapter, "analyze_page", None) or getattr(adapter, "analyze", None)
    if not callable(method):
        raise TypeError("视觉适配器缺少 analyze_page/analyze 方法")
    value = method(image=image, page_number=page_number, page_type=page_type, regions=regions, model=options.model, timeout=options.timeout_seconds)
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError("视觉适配器必须返回 JSON object")
    return value


def _blank_page_decision(adapter: object, *, image: Any, page_number: int, options: PdfVisionOptions) -> dict[str, Any]:
    method = getattr(adapter, "classify_blank_page", None)
    if not callable(method):
        raise TypeError("视觉适配器缺少 classify_blank_page 方法")
    value = method(image=image, page_number=page_number, model=options.model, timeout=options.timeout_seconds)
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError("空白页视觉判定必须返回 JSON object")
    classification = value.get("classification")
    if classification not in {"blank", "non_blank", "uncertain"}:
        raise ValueError("空白页视觉判定 classification 无效")
    confidence = float(value.get("confidence"))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("空白页视觉判定 confidence 无效")
    for key in ("has_readable_text", "has_graphics", "has_table", "has_stamp_or_annotation"):
        if not isinstance(value.get(key), bool):
            raise ValueError(f"空白页视觉判定 {key} 无效")
    return {"classification": classification, "confidence": confidence, **{key: value[key] for key in ("has_readable_text", "has_graphics", "has_table", "has_stamp_or_annotation")}, "reason": str(value.get("reason") or "")[:500], "model": options.model}


def _vision_regions_for_page(preflight: dict[str, Any], page_number: int) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for image in preflight["images"]:
        if image.bbox:
            regions.append({"bbox": image.bbox, "region_type": "figure", "need_vision": True, "image_id": image.image_id})
    if preflight["has_table"] and not regions:
        regions.append({"bbox": None, "region_type": "table", "need_vision": True})
    if not regions:
        regions.append({"bbox": None, "region_type": "page", "need_vision": True})
    for index, region in enumerate(regions):
        region["region_id"] = f"page:{page_number}:region:{index}"
    return regions


def _apply_vision(*, source: Path, page_number: int, routing_type: str, preflight: dict[str, Any], options: PdfVisionOptions, document_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not options.enabled:
        return [], []
    if not options.api_key and options.adapter is None:
        return [], ["视觉增强已启用但缺少 API key，已使用确定性路径"]
    try:
        adapter = options.adapter
        if adapter is None:
            adapter = OpenAIResponsesVisionAdapter(base_url=options.base_url, api_key=options.api_key, model="gpt-5.5", timeout=options.timeout_seconds, retries=options.retries)
        rendered = _render_page(source, page_number, 1.5)
        regions = _vision_regions_for_page(preflight, page_number)
        payload = _vision_payload(adapter, image=rendered, page_number=page_number, page_type=routing_type, regions=regions, options=options)
        raw_regions = payload.get("regions") or []
        output: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_regions if isinstance(raw_regions, list) else []):
            if not isinstance(raw, dict):
                continue
            bbox = raw.get("bbox")
            output.append({
                "region_id": f"{document_id}:page:{page_number}:region:{index}",
                "page_number": page_number,
                "bbox": [float(value) for value in bbox] if isinstance(bbox, list) and len(bbox) == 4 else None,
                "region_type": str(raw.get("region_type") or "visual"),
                "ocr_text": str(raw.get("ocr_text") or ""),
                "vision_raw": str(raw.get("vision_raw") or raw.get("raw") or ""),
                "vision_summary": str(raw.get("vision_summary") or raw.get("summary") or ""),
                "vision_search": _search_text(str(raw.get("vision_search") or raw.get("vision_summary") or raw.get("summary") or "")),
                "extraction_method": "vision:vlm",
                "metadata": {"model": options.model, "source": f"page:{page_number}:region:{index}", "model_generated": True},
            })
        return output, []
    except Exception as exc:
        return [], [f"视觉增强失败，已使用确定性路径：{_redacted_error(exc, options.api_key)}"]


def convert_pdf(
    path: str | Path,
    *,
    ocr: object | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    options: PdfConversionOptions | None = None,
) -> DocumentConversion:
    """Convert a PDF while retaining page-level evidence and quality warnings."""
    source = Path(path).resolve()
    opts = options or PdfConversionOptions()
    digest = sha256_file(source)
    document_id = _document_id(source, digest)
    report = QualityReport(optional_backends=_optional_backend_status(opts))
    blocks: list[BlockRecord] = []
    pages: list[PageRecord] = []
    images: list[ImageRecord] = []
    tables: list[TableRecord] = []
    visual_regions: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        import pymupdf

        with pymupdf.open(str(source)) as document:
            report.page_count = int(document.page_count)
            adapter = _ocr_adapter(ocr)
            prepare_runtime = getattr(adapter, "prepare_runtime", None)
            if opts.use_docling and callable(prepare_runtime):
                # Docling may initialize its own RapidOCR sessions before the
                # page-level adapter is first used, so prepare CUDA up front.
                prepare_runtime()
            projection = ""
            docling_page_blocks: dict[int, list[BlockRecord]] = {}
            docling_tables: list[TableRecord] = []
            docling_warning: str | None = None
            preflights = {page_number: _preflight_page(document[page_number - 1], document_id, page_number, opts.low_text_threshold) for page_number in range(1, document.page_count + 1)}
            docling_cell_matching = _docling_cell_matching_enabled(preflights)
            if opts.use_docling:
                projection, docling_page_blocks, docling_tables, docling_warning = _try_docling(
                    source,
                    document_id=document_id,
                    allow_model_download=opts.allow_docling_model_download,
                    cell_matching=docling_cell_matching,
                    device=opts.docling_device,
                    num_threads=opts.docling_num_threads,
                    batch_size=opts.docling_batch_size,
                )
                tables.extend(docling_tables)
                report.table_pages = len({item.page_number for item in tables if item.page_number})
            for page_number in range(1, document.page_count + 1):
                if should_stop is not None and should_stop():
                    status = StageStatus(status="cancelled", error="知识库导入已取消", stage_version=PDF_STAGE_VERSION)
                    return DocumentConversion(
                        document_id=document_id, source_path=str(source), source_name=source.name,
                        source_sha256=digest, extension=source.suffix.lower(), blocks=blocks, pages=pages,
                        images=images, tables=tables, quality_report=report, status=status,
                        metadata={"page_count": report.page_count, "backend": "pymupdf"},
                    )
                page = document[page_number - 1]
                preflight = preflights[page_number]
                page_blocks = list(preflight["native_blocks"] or preflight.get("hidden_blocks") or [])
                native_text = str(preflight["native_text"])
                native_chars = int(preflight["native_chars"])
                hidden_text = str(preflight.get("hidden_text") or "")
                hidden_chars = int(preflight.get("hidden_text_chars") or 0)
                layout_blocks = docling_page_blocks.get(page_number, [])
                docling_text = ""
                docling_chars = 0
                if layout_blocks:
                    page_blocks = list(layout_blocks)
                    docling_text = "\n".join(block.text for block in layout_blocks)
                    docling_chars = _text_char_count(docling_text)
                effective_text = docling_text or native_text or hidden_text
                effective_chars = docling_chars or native_chars or hidden_chars
                docling_ocr_inferred = bool(
                    layout_blocks
                    and native_chars < opts.low_text_threshold
                    and docling_chars >= opts.low_text_threshold
                    and (preflight["images"] or float(preflight["image_area_ratio"]) > 0.0 or hidden_chars > 0)
                )
                hidden_ocr_inferred = bool(not layout_blocks and native_chars < opts.low_text_threshold and hidden_chars >= opts.low_text_threshold)
                page_images = list(preflight["images"])
                images.extend(page_images)
                try:
                    drawing_count = int(preflight["drawing_count"])
                except Exception:
                    drawing_count = 0
                ocr_text = ""
                ocr_items: list[dict[str, Any]] = []
                page_warnings: list[str] = []
                if layout_blocks and opts.review_artistic_headers:
                    header_stats, header_warnings = _review_artistic_headers(
                        source=source,
                        page=page,
                        page_number=page_number,
                        page_blocks=page_blocks,
                        adapter=adapter,
                        options=opts,
                    )
                    for key, value in header_stats.items():
                        report.header_review_metrics[key] = int(report.header_review_metrics.get(key, 0)) + int(value)
                    page_warnings.extend(header_warnings)
                blank_review: dict[str, Any] = {}
                intentional_blank = False
                scan_noise_only = False
                rendered_for_page: Any | None = None
                docling_table_on_page = any(table.page_number == page_number and bool(table_html(table)) for table in docling_tables)
                scan_noise_candidate = bool(
                    opts.detect_scan_noise_pages
                    and (
                        preflight["images"]
                        or float(preflight["image_area_ratio"]) >= 0.70
                        or preflight["routing_type"] == "blank_or_unreadable"
                    )
                    and effective_chars <= SCAN_NOISE_MAX_TEXT_CHARS
                )
                if scan_noise_candidate:
                    try:
                        rendered_for_page = _render_page(source, page_number, opts.scan_noise_render_scale)
                        blank_review = _scan_noise_review(
                            rendered_for_page,
                            page_blocks=page_blocks,
                            page_width=float(page.rect.width),
                            page_height=float(page.rect.height),
                            # A full-page scan image border can resemble a grid in
                            # PDF drawing metadata. Only a structured table is
                            # strong enough to veto near-blank detection here.
                            has_table=docling_table_on_page,
                        )
                        scan_noise_only = blank_review.get("classification") == "scan_noise_only" and float(blank_review.get("confidence", 0.0)) >= 0.95
                        if scan_noise_only:
                            page_blocks = []
                            effective_text = ""
                            effective_chars = 0
                            report.scan_noise_only_pages.append(page_number)
                            report.blank_page_reviews.append({"page_number": page_number, **blank_review})
                    except Exception as exc:
                        blank_review = {"classification": "uncertain", "confidence": 0.0, "reason": f"本地近空白检测失败：{type(exc).__name__}"}
                if not scan_noise_only and preflight["routing_type"] == "blank_or_unreadable" and opts.remote_blank_review:
                    try:
                        if opts.vision is None:
                            raise RuntimeError("未配置当前 GUI Responses API")
                        if not opts.vision.api_key and opts.vision.adapter is None:
                            raise ValueError("当前 GUI Responses API key 为空")
                        vision_adapter = opts.vision.adapter or OpenAIResponsesVisionAdapter(
                            base_url=opts.vision.base_url,
                            api_key=opts.vision.api_key,
                            model=opts.vision.model,
                            timeout=opts.vision.timeout_seconds,
                            retries=opts.vision.retries,
                        )
                        rendered = rendered_for_page if rendered_for_page is not None else _render_page(source, page_number, 1.5)
                        blank_review = _blank_page_decision(vision_adapter, image=rendered, page_number=page_number, options=opts.vision)
                        report.blank_page_reviews.append({"page_number": page_number, **blank_review})
                        intentional_blank = bool(
                            blank_review["classification"] == "blank"
                            and float(blank_review["confidence"]) >= 0.95
                            and not blank_review["has_readable_text"]
                            and not blank_review["has_graphics"]
                            and not blank_review["has_table"]
                            and not blank_review["has_stamp_or_annotation"]
                        )
                    except Exception as exc:
                        page_warnings.append(f"空白页远程判定失败，继续 OCR：{_redacted_error(exc, (opts.vision.api_key if opts.vision else ''))}")
                ocr_required = (not intentional_blank) and (not scan_noise_only) and not _usable(effective_text, opts.low_text_threshold) and preflight["routing_type"] in {"scanned", "mixed", "blank_or_unreadable"}
                if ocr_required:
                    try:
                        rendered = rendered_for_page if rendered_for_page is not None and opts.scan_noise_render_scale == opts.render_scale else _render_page(source, page_number, opts.render_scale)
                        ocr_items = _structured_ocr_result(adapter, rendered)
                        ocr_text = _display_text("\n".join(item["text"] for item in ocr_items)) if ocr_items else _display_text(adapter.recognize(rendered))
                    except Exception as exc:
                        page_warnings.append(f"OCR 失败：{type(exc).__name__}: {exc}")
                ocr_chars = _text_char_count(ocr_text)
                extraction_method = "layout:docling+ocr-inferred" if docling_ocr_inferred else ("layout:docling" if layout_blocks else ("ocr:hidden-pdf-text" if hidden_ocr_inferred else "native:pymupdf"))
                if ocr_text and effective_chars:
                    extraction_method = f"{extraction_method}+ocr:rapidocr"
                    if ocr_items:
                        page_blocks.extend(BlockRecord(block_id="", location="", text=item["text"], block_type="ocr_line", extraction_method="ocr:rapidocr", confidence=item["confidence"], bbox=item["bbox"], metadata={"text_raw": item["text"], "text_display": item["text"], "text_search": _search_text(item["text"]), "ocr_order": item["order"]}) for item in ocr_items)
                    else:
                        page_blocks.append(BlockRecord(block_id="", location="", text=ocr_text, block_type="ocr", extraction_method="ocr:rapidocr", metadata={"text_raw": ocr_text, "text_display": ocr_text, "text_search": _search_text(ocr_text)}))
                elif ocr_text:
                    extraction_method = "ocr:rapidocr"
                    page_blocks = [BlockRecord(block_id="", location="", text=item["text"], block_type="ocr_line", extraction_method="ocr:rapidocr", confidence=item["confidence"], bbox=item["bbox"], metadata={"text_raw": item["text"], "text_display": item["text"], "text_search": _search_text(item["text"]), "ocr_order": item["order"]}) for item in ocr_items] or [BlockRecord(block_id="", location="", text=ocr_text, block_type="ocr", extraction_method="ocr:rapidocr", metadata={"text_raw": ocr_text, "text_display": ocr_text, "text_search": _search_text(ocr_text)})]
                elif not native_chars and not page_blocks and not intentional_blank and not scan_noise_only:
                    extraction_method = "none"
                    page_warnings.append("页面没有可用原生文本或 OCR 文本")
                unrecovered = (not intentional_blank) and (not scan_noise_only) and ocr_required and not _usable(effective_text, opts.low_text_threshold) and not _usable(ocr_text, opts.low_text_threshold)
                if unrecovered:
                    report.failed_pages.append(page_number)
                    if "页面没有可用原生文本或 OCR 文本" not in page_warnings:
                        page_warnings.append("页面没有可用原生文本或 OCR 文本")
                if effective_text and _encoding_anomaly(effective_text):
                    page_warnings.append("提取文本疑似字体编码异常，建议复核版面/OCR后端")
                candidates = _table_candidates(
                    page,
                    document_id=document_id,
                    page_number=page_number,
                    drawing_count=drawing_count,
                    native_chars=native_chars,
                    image_area_ratio=float(preflight["image_area_ratio"]),
                    regions=list(preflight.get("vector_table_regions") or []),
                    enabled=(not scan_noise_only) and opts.detect_table_candidates and bool(preflight.get("has_table")),
                )
                tables.extend(candidates)
                routing_type, classification_reason = _classify_routing(native_chars=native_chars, ocr_chars=ocr_chars, image_count=len(page_images), drawing_count=drawing_count, image_area_ratio=float(preflight["image_area_ratio"]), layout_complexity=float(preflight["layout_complexity"]), has_table=bool(candidates or docling_table_on_page))
                if intentional_blank:
                    routing_type = "intentional_blank"
                    classification_reason = "当前 GUI Responses API 高置信度确认原始空白页"
                    extraction_method = "remote:gui-responses-blank-review"
                    page_blocks = []
                    report.intentional_blank_pages.append(page_number)
                elif scan_noise_only:
                    routing_type = "scan_noise_only"
                    classification_reason = str(blank_review.get("reason") or "本地检测确认页面仅含扫描噪声")
                    extraction_method = "local:scan-noise-detection"
                    page_blocks = []
                elif layout_blocks and not ocr_chars:
                    if docling_ocr_inferred:
                        routing_type = "scanned"
                        classification_reason = "PDF 原生文字不足，Docling 从图像页面提取到可用文字"
                    else:
                        routing_type = "native_table" if candidates or docling_table_on_page else ("figure_page" if preflight["image_area_ratio"] >= 0.55 else "native_text")
                elif hidden_ocr_inferred and not ocr_chars:
                    routing_type = "scanned"
                    classification_reason = "PDF 仅包含不可见 OCR 文字层，按扫描页处理"
                # Keep the legacy page_type values for v2 consumers; routing_type is the plan-level classifier.
                page_type = {"native_text": "native", "native_table": "native", "complex_layout": "mixed", "figure_page": "mixed"}.get(routing_type, routing_type)
                vision_for_page, vision_warnings = _apply_vision(source=source, page_number=page_number, routing_type=routing_type, preflight=preflight, options=opts.vision or PdfVisionOptions(), document_id=document_id) if routing_type in {"complex_layout", "figure_page", "scanned", "blank_or_unreadable"} and not intentional_blank and not scan_noise_only else ([], [])
                if vision_for_page:
                    visual_regions.extend(vision_for_page)
                    for region in vision_for_page:
                        for image in page_images:
                            if region.get("image_id") == image.image_id or (region.get("bbox") and image.bbox and region["bbox"] == image.bbox):
                                image.vision_raw = region.get("vision_raw", "")
                                image.vision_summary = region.get("vision_summary", "")
                                image.vision_search = region.get("vision_search", "")
                page_warnings.extend(vision_warnings)
                needs_review = bool(page_warnings or candidates or unrecovered or page_type == "blank_or_unreadable") and not intentional_blank and not scan_noise_only
                if page_type in {"scanned", "mixed"} and not needs_review and (docling_ocr_inferred or hidden_ocr_inferred or _usable(ocr_text, opts.low_text_threshold)):
                    report.successfully_recovered_ocr_pages.append(page_number)
                native_block_count = int(preflight.get("native_block_count", 0))
                text_source = "none" if scan_noise_only or intentional_blank else ("docling_ocr_inferred" if docling_ocr_inferred else ("docling_layout" if layout_blocks else ("hidden_pdf_ocr" if hidden_ocr_inferred else ("rapidocr" if ocr_text else "pymupdf_native"))))
                page_record = PageRecord(page_number=page_number, width=float(page.rect.width), height=float(page.rect.height), rotation=int(page.rotation), page_type=page_type, routing_type=routing_type, extraction_method=extraction_method, native_text_chars=native_chars, native_text_blocks=native_block_count, hidden_text_chars=hidden_chars, docling_text_chars=0 if scan_noise_only else docling_chars, text_source=text_source, ocr_text_chars=ocr_chars, image_count=len(page_images), drawing_count=drawing_count, table_candidate_count=len(candidates), image_area_ratio=float(preflight["image_area_ratio"]), text_density=float(preflight["text_density"]), encoding_anomaly=bool(preflight["encoding_anomaly"]), layout_complexity=float(preflight["layout_complexity"]), foreground_ratio=float(blank_review.get("foreground_ratio", 0.0)), significant_component_count=int(blank_review.get("significant_component_count", 0)), noise_component_count=int(blank_review.get("noise_component_count", 0)), classification_reason=classification_reason, has_table_candidate=bool(candidates or docling_table_on_page), has_figure_candidate=False if scan_noise_only else bool(preflight["image_area_ratio"] > 0 or page_images), needs_review=needs_review, warnings=page_warnings, blank_review=blank_review)
                pages.append(page_record)
                if page_warnings:
                    warnings.extend([f"第 {page_number} 页：{item}" for item in page_warnings])
                    report.low_quality_pages.append(page_number)
                if page_type == "native":
                    report.native_pages += 1
                elif page_type == "scanned":
                    report.ocr_pages += 1
                elif page_type == "mixed":
                    report.mixed_pages += 1
                if page_images:
                    report.image_pages += 1
                if candidates:
                    report.table_pages += 1
                for item_index, block in enumerate(page_blocks):
                    if not block.text.strip():
                        continue
                    block.block_id = f"{document_id}:page:{page_number}:block:{item_index}"
                    block.location = f"page:{page_number}:block:{item_index}"
                    block.page_number = page_number
                    blocks.append(block)
                if progress is not None:
                    progress(page_number, document.page_count)
            if docling_warning:
                warnings.append(docling_warning)
                report.warnings.append(docling_warning)
            if projection and not docling_page_blocks:
                blocks.append(
                    BlockRecord(
                        block_id=f"{document_id}:document:docling",
                        location="document:docling",
                        text=projection,
                        block_type="document_projection",
                        extraction_method="layout:docling",
                        metadata={"text_raw": projection, "text_display": _display_text(projection), "text_search": _search_text(projection)},
                    )
                )
                # Projection is already emitted once above when page-level
                # Docling blocks are unavailable; do not duplicate it here.
            table_pages = {page.page_number for page in pages if page.routing_type in {"native_table", "complex_layout", "mixed"} and page.table_candidate_count > 0}
            extracted_tables, table_warnings = _try_optional_tables(source, opts, document_id, page_numbers=table_pages)
            if extracted_tables:
                tables.extend(extracted_tables)
                report.table_pages = len({item.page_number for item in tables})
            if table_warnings:
                warnings.extend(table_warnings)
        excluded_table_pages = {*report.intentional_blank_pages, *report.scan_noise_only_pages}
        tables = _dedupe_tables([table for table in tables if table.page_number not in excluded_table_pages])
        report.table_pages = len({item.page_number for item in tables if item.page_number})
        report.warnings.extend(warnings)
        confidence_values = [float(block.confidence) for block in blocks if block.confidence is not None]
        low_confidence_count = sum(value < 0.65 for value in confidence_values)
        report.text_metrics = {"native_text_chars": sum(page.native_text_chars for page in pages), "hidden_text_chars": sum(page.hidden_text_chars for page in pages), "docling_text_chars": sum(page.docling_text_chars for page in pages), "ocr_text_chars": sum(page.ocr_text_chars for page in pages), "text_block_count": len(blocks), "encoding_anomaly_pages": sum(bool(page.encoding_anomaly) for page in pages), "docling_ocr_inferred_pages": sum(page.text_source == "docling_ocr_inferred" for page in pages), "hidden_pdf_ocr_pages": sum(page.text_source == "hidden_pdf_ocr" for page in pages), "ocr_confidence_observation_count": len(confidence_values), "ocr_confidence_coverage_ratio": round(len(confidence_values) / len(blocks), 4) if blocks else 0.0, "mean_ocr_confidence": round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else None, "low_confidence_ocr_block_count": low_confidence_count, "low_confidence_ocr_block_ratio": round(low_confidence_count / len(confidence_values), 4) if confidence_values else 0.0, "ocr_low_confidence_threshold": 0.65}
        table_count = len(tables)
        valid_table_count = sum(bool(table_html(table)) for table in tables)
        candidate_count = sum(table.extraction_method == "candidate:vector-drawings" for table in tables)
        needs_review_count = sum(bool(table.needs_review) for table in tables)
        report.table_metrics = {
            "table_count": table_count,
            "structured_table_count": table_count - candidate_count,
            "candidate_count": candidate_count,
            "html_valid_count": valid_table_count,
            "invalid_table_count": table_count - valid_table_count,
            "needs_review_count": needs_review_count,
            "confirmed_table_count": sum(bool(table_html(table)) and not table.needs_review for table in tables),
            "html_valid_ratio": round(valid_table_count / table_count, 4) if table_count else 1.0,
            "needs_review_ratio": round(needs_review_count / table_count, 4) if table_count else 0.0,
            "table_conflict_count": sum("table_source_conflict" in (table.metadata.get("quality_flags") or []) for table in tables),
        }
        report.vision_metrics = {"region_count": len(visual_regions), "successful_regions": sum(bool(item.get("vision_summary") or item.get("ocr_text")) for item in visual_regions), "models": sorted({str((item.get("metadata") or {}).get("model")) for item in visual_regions if (item.get("metadata") or {}).get("model")})}
        report.header_review_metrics.setdefault("review_count", 0)
        report.header_review_metrics.setdefault("recovered_count", 0)
        report.header_review_metrics.setdefault("confirmed_count", 0)
        report.header_review_metrics.setdefault("needs_review_count", 0)
        report.header_review_metrics["method"] = "rapidocr-local-region"
        report.header_review_metrics["enabled"] = bool(opts.review_artistic_headers)
        _finalize_quality_report(report)
        if not blocks and report.effective_content_pages > 0:
            raise ValueError("PDF 没有可提取的文字")
        # Quality warnings are machine-readable diagnostics, not a human gate.
        status_name = "success_with_warnings" if (report.needs_review or warnings) else "ready"
        backend = "docling+pymupdf" if any(block.extraction_method == "layout:docling" for block in blocks) else "pymupdf"
        return DocumentConversion(document_id=document_id, source_path=str(source), source_name=source.name, source_sha256=digest, extension=source.suffix.lower(), blocks=blocks, pages=pages, images=images, tables=tables, visual_regions=visual_regions, quality_report=report, status=StageStatus(status=status_name, warnings=warnings, stage_version=PDF_STAGE_VERSION), metadata={"page_count": report.page_count, "backend": backend, "text_projections": ["text_raw", "text_display", "text_search"], "table_body": "html", "vision_enabled": bool(opts.vision and opts.vision.enabled), "docling_cell_matching": docling_cell_matching if opts.use_docling else None})
    except Exception as exc:
        report.warnings.extend(warnings)
        _finalize_quality_report(report)
        return DocumentConversion(document_id=document_id, source_path=str(source), source_name=source.name, source_sha256=digest, extension=source.suffix.lower(), blocks=blocks, pages=pages, images=images, tables=tables, quality_report=report, status=StageStatus(status="failed", error=f"{type(exc).__name__}: {exc}", warnings=warnings, stage_version=PDF_STAGE_VERSION), metadata={"page_count": report.page_count, "backend": "pymupdf"})


def convert_document(path: str | Path, **kwargs: Any) -> DocumentConversion:
    source = Path(path).resolve()
    if source.suffix.lower() == ".pdf":
        return convert_pdf(source, **kwargs)
    _ensure_project_imports()
    from runtime.knowledge_base import extract_document

    # PDF-only options must not leak into the legacy DOCX extractor.
    kwargs = {key: value for key, value in kwargs.items() if key in {"ocr", "progress", "should_stop"}}
    digest = sha256_file(source)
    document_id = _document_id(source, digest)
    try:
        located = extract_document(source, **kwargs)
        blocks = [BlockRecord(block_id=f"{document_id}:{location}", location=location, text=text, block_type="paragraph", extraction_method="docx:xml") for location, text in located]
        status = StageStatus(status="ready", stage_version="document-convert.v1")
    except Exception as exc:
        blocks = []
        status = StageStatus(status="failed", error=f"{type(exc).__name__}: {exc}", stage_version="document-convert.v1")
    return DocumentConversion(document_id=document_id, source_path=str(source), source_name=source.name, source_sha256=digest, extension=source.suffix.lower(), blocks=blocks, status=status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a PDF/DOCX into knowledge-pipeline JSON")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--strict-quality", action="store_true", help="兼容旧参数；质量 warning 不阻断自动处理")
    parser.add_argument("--docling", dest="docling", action="store_true", default=True)
    parser.add_argument("--no-docling", dest="docling", action="store_false")
    parser.add_argument("--camelot", dest="camelot", action="store_true", default=True)
    parser.add_argument("--no-camelot", dest="camelot", action="store_false")
    parser.add_argument("--pdfplumber", dest="pdfplumber", action="store_true", default=True)
    parser.add_argument("--no-pdfplumber", dest="pdfplumber", action="store_false")
    parser.add_argument("--allow-docling-model-download", dest="allow_docling_model_download", action="store_true", default=True, help="允许 Docling 在线下载模型（默认开启）")
    parser.add_argument("--offline-docling", dest="allow_docling_model_download", action="store_false", help="禁止 Docling 下载模型，使用已缓存模型或回退")
    parser.add_argument("--vision", action="store_true", help="对复杂/扫描/图表页面启用可选视觉增强（从 GUI 设置读取 URL/key，模型固定为 gpt-5.5）")
    args = parser.parse_args(argv)
    vision = PdfVisionOptions.from_gui_settings(enabled=True) if args.vision else None
    result = convert_document(args.input, options=PdfConversionOptions(use_docling=args.docling, use_camelot=args.camelot, use_pdfplumber=args.pdfplumber, strict_quality=args.strict_quality, allow_docling_model_download=args.allow_docling_model_download, vision=vision))
    write_json(str(args.output), result.to_dict())
    return 0 if result.status.status in {"ready", "success_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

