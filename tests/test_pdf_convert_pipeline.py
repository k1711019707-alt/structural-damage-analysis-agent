from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest


class FakeOcr:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls = 0

    def recognize(self, _image: object) -> str:
        self.calls += 1
        return self.text


class FakeBlankVision:
    def __init__(self, decision: dict[str, object]) -> None:
        self.decision = decision
        self.calls = 0

    def classify_blank_page(self, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        return dict(self.decision)


def _pdf(path: Path, text: str | None = None) -> None:
    document = pymupdf.open()
    page = document.new_page()
    if text:
        page.insert_text((72, 72), text)
    document.save(str(path))
    document.close()


def _image_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 800), False)
    pixmap.clear_with(255)
    page.insert_image(page.rect, pixmap=pixmap)
    document.save(str(path))
    document.close()


def _raster_pdf(path: Path, image: np.ndarray) -> None:
    height, width = image.shape[:2]
    rgb = image if image.ndim == 3 else np.repeat(image[:, :, None], 3, axis=2)
    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, width, height, rgb.astype(np.uint8).tobytes(), False)
    page.insert_image(page.rect, pixmap=pixmap)
    document.save(str(path))
    document.close()


def test_native_pdf_emits_page_inventory_and_bbox(tmp_path: Path) -> None:
    from knowledge_pipeline.pdf_convert import convert_pdf

    path = tmp_path / "native.pdf"
    _pdf(path, "Concrete inspection standard native text")
    result = convert_pdf(path, ocr=FakeOcr("must not run"))

    assert result.status.status == "ready"
    assert result.pages[0].page_type == "native"
    assert result.pages[0].native_text_blocks == 1
    assert result.blocks[0].bbox is not None
    assert result.blocks[0].metadata["text_search"]


def test_blank_pdf_can_use_successful_ocr_without_source_only_quality_warning(tmp_path: Path, monkeypatch) -> None:
    from knowledge_pipeline import pdf_convert

    path = tmp_path / "scan.pdf"
    _pdf(path)
    monkeypatch.setattr(pdf_convert, "_render_page", lambda *_args: object())
    result = pdf_convert.convert_pdf(path, ocr=FakeOcr("scanned page text"))

    assert result.status.status == "ready"
    assert result.pages[0].page_type == "scanned"
    assert result.pages[0].extraction_method == "ocr:rapidocr"
    assert result.quality_report.ocr_pages == 1
    assert result.quality_report.low_quality_pages == []
    assert result.quality_report.quality_score == 1.0


@pytest.mark.parametrize(
    "output",
    [
        SimpleNamespace(
            boxes=np.array([[[1, 2], [9, 2], [9, 8], [1, 8]]], dtype=float),
            txts=np.array(["数组 OCR 文本"]),
            scores=np.array([0.91]),
        ),
        {
            "polys": np.array([[[2, 3], [10, 3], [10, 9], [2, 9]]], dtype=float),
            "texts": np.array(["字典 OCR 文本"]),
            "scores_list": np.array([0.87]),
        },
        [[[[3, 4], [11, 4], [11, 10], [3, 10]], "列表 OCR 文本", 0.83]],
        np.array(
            [[[[4, 5], [12, 5], [12, 11], [4, 11]], "矩阵 OCR 文本", 0.79]],
            dtype=object,
        ),
    ],
)
def test_structured_ocr_normalizes_object_dict_list_and_ndarray(output: object) -> None:
    from knowledge_pipeline.pdf_convert import _structured_ocr_result


    class Adapter:
        def _get_engine(self):
            return lambda _image: output

    records = _structured_ocr_result(Adapter(), object())

    assert len(records) == 1
    assert records[0]["text"].endswith("OCR 文本")
    assert records[0]["bbox"] is not None
    assert records[0]["confidence"] == pytest.approx(float(records[0]["confidence"]))


def test_local_header_review_recovers_artistic_text_and_rejects_partial_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    from knowledge_pipeline import pdf_convert
    from knowledge_pipeline.contracts import BlockRecord

    rendered = np.zeros((800, 600, 3), dtype=np.uint8)
    monkeypatch.setattr(pdf_convert, "_render_page", lambda *_args: rendered)
    monkeypatch.setattr(
        pdf_convert,
        "_structured_ocr_result",
        lambda *_args: [
            {"text": "建材发展导向", "bbox": [100, 70, 200, 100], "confidence": 0.94, "order": 0},
            {"text": "DEVELOPMENT GUIDE TO", "bbox": [220, 70, 520, 100], "confidence": 0.99, "order": 1},
        ],
    )
    blocks = [
        BlockRecord(
            block_id="",
            location="",
            text="建材发展导白",
            bbox=[100, 700, 200, 730],
            extraction_method="layout:docling",
            metadata={"docling_label": "page_header", "text_raw": "建材发展导白"},
        ),
        BlockRecord(
            block_id="",
            location="",
            text="DEVELOPMENT GUIDE TO BUILDING MATERIALS",
            bbox=[220, 700, 520, 730],
            extraction_method="layout:docling",
            metadata={"docling_label": "page_header", "text_raw": "DEVELOPMENT GUIDE TO BUILDING MATERIALS"},
        ),
    ]

    stats, warnings = pdf_convert._review_artistic_headers(
        source=Path("header.pdf"),
        page=SimpleNamespace(rect=SimpleNamespace(width=600, height=800)),
        page_number=1,
        page_blocks=blocks,
        adapter=object(),
        options=pdf_convert.PdfConversionOptions(),
    )

    assert warnings == []
    assert blocks[0].text == "建材发展导向"
    assert blocks[0].metadata["text_raw"] == "建材发展导白"
    assert blocks[0].metadata["header_review"]["status"] == "recovered"
    assert blocks[1].text == "DEVELOPMENT GUIDE TO BUILDING MATERIALS"
    assert blocks[1].metadata["header_review"]["status"] == "needs_review"
    assert stats == {"review_count": 2, "recovered_count": 1, "confirmed_count": 0, "needs_review_count": 1}


def test_vector_table_regions_require_grid_topology_and_use_local_bbox() -> None:
    from knowledge_pipeline.pdf_convert import _detect_vector_table_regions

    decorative = SimpleNamespace(
        rect=pymupdf.Rect(0, 0, 600, 800),
        get_drawings=lambda: [{"items": [("l", pymupdf.Point(50, y), pymupdf.Point(550, y)) for y in (60, 100, 140, 180, 220)]}],
    )
    assert _detect_vector_table_regions(decorative) == []

    grid_items = [
        *(('l', pymupdf.Point(100, y), pymupdf.Point(300, y)) for y in (100, 150, 200)),
        *(('l', pymupdf.Point(x, 100), pymupdf.Point(x, 200)) for x in (100, 200, 300)),
    ]
    grid = SimpleNamespace(
        rect=pymupdf.Rect(0, 0, 600, 800),
        get_drawings=lambda: [{"items": grid_items}],
    )
    regions = _detect_vector_table_regions(grid)

    assert len(regions) == 1
    assert regions[0]["bbox"] == pytest.approx([100.0, 100.0, 300.0, 200.0])
    assert regions[0]["horizontal_line_count"] == 3
    assert regions[0]["vertical_line_count"] == 3
    assert regions[0]["intersection_count"] == 9

    tiny_grid_items = [
        *(('l', pymupdf.Point(100, y), pymupdf.Point(140, y)) for y in (100, 120, 140)),
        *(('l', pymupdf.Point(x, 100), pymupdf.Point(x, 140)) for x in (100, 120, 140)),
    ]
    tiny_grid = SimpleNamespace(
        rect=pymupdf.Rect(0, 0, 600, 800),
        get_drawings=lambda: [{"items": tiny_grid_items}],
    )
    assert _detect_vector_table_regions(tiny_grid) == []


def test_image_dominant_page_suppresses_vector_table_candidate() -> None:
    from knowledge_pipeline.pdf_convert import _table_candidates

    regions = [{"bbox": [100.0, 100.0, 300.0, 200.0], "horizontal_line_count": 3, "vertical_line_count": 3, "intersection_count": 9}]
    page = SimpleNamespace(rect=pymupdf.Rect(0, 0, 600, 800))

    assert _table_candidates(
        page,
        document_id="doc",
        page_number=1,
        drawing_count=6,
        native_chars=0,
        image_area_ratio=0.95,
        regions=regions,
    ) == []


def test_scan_image_border_hint_without_candidate_is_not_reported_as_table(tmp_path: Path, monkeypatch) -> None:
    from knowledge_pipeline import pdf_convert
    from knowledge_pipeline.contracts import BlockRecord

    path = tmp_path / "scan-border.pdf"
    image = np.full((800, 600, 3), 255, dtype=np.uint8)
    image[399:402, 299:302] = 210
    _raster_pdf(path, image)
    false_block = BlockRecord(block_id="", location="", text="1", bbox=[299.0, 399.0, 302.0, 402.0], extraction_method="layout:docling")
    monkeypatch.setattr(pdf_convert, "_try_docling", lambda *_args, **_kwargs: ("", {1: [false_block]}, [], None))
    original_preflight = pdf_convert._preflight_page

    def forced_table_hint(*args, **kwargs):
        result = original_preflight(*args, **kwargs)
        result["has_table"] = True
        result["vector_table_regions"] = []
        return result

    monkeypatch.setattr(pdf_convert, "_preflight_page", forced_table_hint)

    result = pdf_convert.convert_pdf(
        path,
        ocr=FakeOcr("must not run"),
        options=pdf_convert.PdfConversionOptions(use_camelot=False, use_pdfplumber=False),
    )

    assert result.pages[0].page_type == "scan_noise_only"
    assert result.pages[0].has_table_candidate is False
    assert result.quality_report.scan_noise_only_pages == [1]


def test_docling_text_on_image_page_is_reported_as_scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from knowledge_pipeline import pdf_convert
    from knowledge_pipeline.contracts import BlockRecord

    path = tmp_path / "image.pdf"
    _image_pdf(path)
    layout_block = BlockRecord(
        block_id="",
        location="",
        text="扫描页面的 Docling OCR 有效文本",
        extraction_method="layout:docling",
        bbox=[72.0, 72.0, 300.0, 100.0],
    )
    monkeypatch.setattr(pdf_convert, "_try_docling", lambda *_args, **_kwargs: ("", {1: [layout_block]}, [], None))

    result = pdf_convert.convert_pdf(
        path,
        ocr=FakeOcr("must not run"),
        options=pdf_convert.PdfConversionOptions(use_camelot=False, use_pdfplumber=False, detect_table_candidates=False),
    )

    assert result.pages[0].page_type == "scanned"
    assert result.pages[0].routing_type == "scanned"
    assert result.pages[0].native_text_chars == 0
    assert result.pages[0].docling_text_chars > 0
    assert result.pages[0].extraction_method == "layout:docling+ocr-inferred"


def test_invisible_pdf_text_is_hidden_ocr_not_native(tmp_path: Path) -> None:
    from knowledge_pipeline.pdf_convert import _preflight_page

    path = tmp_path / "hidden-ocr.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 800), False)
    pixmap.clear_with(255)
    page.insert_image(page.rect, pixmap=pixmap)
    page.insert_text((72, 72), "hidden OCR text layer", fill_opacity=0)
    document.save(str(path))
    document.close()

    with pymupdf.open(str(path)) as reopened:
        preflight = _preflight_page(reopened[0], "doc", 1, 12)

    assert preflight["native_chars"] == 0
    assert preflight["hidden_text_chars"] > 0
    assert preflight["routing_type"] == "scanned"
    assert preflight["image_area_ratio"] > 0.90


def test_scan_dominant_policy_disables_docling_cell_matching() -> None:
    from knowledge_pipeline.pdf_convert import _docling_cell_matching_enabled

    mostly_scanned = {index: {"routing_type": "scanned"} for index in range(1, 9)}
    mostly_scanned.update({9: {"routing_type": "native_text"}, 10: {"routing_type": "native_text"}})
    mostly_native = {1: {"routing_type": "scanned"}, 2: {"routing_type": "native_text"}}
    repeated_scan_geometry = {
        index: {"routing_type": "native_text", "drawing_count": 95 + (index % 2)}
        for index in range(1, 11)
    }

    assert _docling_cell_matching_enabled(mostly_scanned) is False
    assert _docling_cell_matching_enabled(mostly_native) is True
    assert _docling_cell_matching_enabled(repeated_scan_geometry) is False


def test_overlapping_empty_vector_candidate_is_folded_into_structured_table() -> None:
    from knowledge_pipeline.contracts import TableRecord
    from knowledge_pipeline.pdf_convert import _dedupe_tables

    structured = TableRecord(
        table_id="structured",
        page_number=1,
        bbox=[100.0, 100.0, 300.0, 200.0],
        html="<table><tr><td>A</td></tr></table>",
        extraction_method="table:docling",
        needs_review=False,
    )
    candidate = TableRecord(
        table_id="candidate",
        page_number=1,
        bbox=[95.0, 95.0, 305.0, 205.0],
        extraction_method="candidate:vector-drawings",
    )

    result = _dedupe_tables([candidate, structured])

    assert [table.table_id for table in result] == ["structured"]
    assert structured.metadata["alternative_sources"][0]["table_id"] == "candidate"


def test_optional_tables_short_circuit_without_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    from knowledge_pipeline import pdf_convert

    monkeypatch.setattr(pdf_convert.importlib.util, "find_spec", lambda _name: pytest.fail("optional backend should not be inspected"))
    records, warnings = pdf_convert._try_optional_tables(
        Path("unused.pdf"),
        pdf_convert.PdfConversionOptions(),
        "doc",
        page_numbers=set(),
    )

    assert records == []
    assert warnings == []


def test_table_quality_reduces_score_for_unresolved_candidates() -> None:
    from knowledge_pipeline.contracts import QualityReport
    from knowledge_pipeline.pdf_convert import _quality_score

    report = QualityReport(
        page_count=10,
        table_metrics={"table_count": 10, "invalid_table_count": 8, "needs_review_count": 8},
    )

    assert _quality_score(report) < 1.0


def test_scan_noise_detector_accepts_tiny_speck_but_rejects_meaningful_sparse_region() -> None:
    from knowledge_pipeline.contracts import BlockRecord
    from knowledge_pipeline.pdf_convert import _scan_noise_review

    tiny_speck = np.full((800, 600, 3), 255, dtype=np.uint8)
    tiny_speck[399:402, 299:302] = 210
    micro_block = BlockRecord(
        block_id="",
        location="",
        text="1",
        bbox=[299.0, 399.0, 302.0, 402.0],
        extraction_method="layout:docling",
    )

    noise = _scan_noise_review(
        tiny_speck,
        page_blocks=[micro_block],
        page_width=600,
        page_height=800,
        has_table=False,
    )

    assert noise["classification"] == "scan_noise_only"
    assert noise["confidence"] >= 0.95
    assert noise["suppressed_text"] == ["1"]

    sparse_content = tiny_speck.copy()
    sparse_content[80:110, 100:300] = 0
    meaningful = _scan_noise_review(
        sparse_content,
        page_blocks=[micro_block],
        page_width=600,
        page_height=800,
        has_table=False,
    )

    assert meaningful["classification"] != "scan_noise_only"


def test_scan_noise_page_suppresses_false_docling_block_and_is_not_failed(tmp_path: Path, monkeypatch) -> None:
    from knowledge_pipeline import pdf_convert
    from knowledge_pipeline.contracts import BlockRecord

    path = tmp_path / "speck.pdf"
    image = np.full((800, 600, 3), 255, dtype=np.uint8)
    image[399:402, 299:302] = 210
    _raster_pdf(path, image)
    false_block = BlockRecord(
        block_id="",
        location="",
        text="1",
        block_type="list_item",
        extraction_method="layout:docling",
        bbox=[299.0, 399.0, 302.0, 402.0],
        metadata={"docling_label": "list_item"},
    )
    monkeypatch.setattr(pdf_convert, "_try_docling", lambda *_args, **_kwargs: ("", {1: [false_block]}, [], None))
    ocr = FakeOcr("must not run")

    result = pdf_convert.convert_pdf(
        path,
        ocr=ocr,
        options=pdf_convert.PdfConversionOptions(use_camelot=False, use_pdfplumber=False),
    )

    assert ocr.calls == 0
    assert result.pages[0].page_type == "scan_noise_only"
    assert result.pages[0].routing_type == "scan_noise_only"
    assert result.pages[0].has_table_candidate is False
    assert result.pages[0].needs_review is False
    assert result.pages[0].blank_review["suppressed_text"] == ["1"]
    assert result.blocks == []
    assert result.tables == []
    assert result.quality_report.scan_noise_only_pages == [1]
    assert result.quality_report.failed_pages == []
    assert result.quality_report.low_quality_pages == []
    assert result.quality_report.effective_content_pages == 0
    assert result.quality_report.quality_score == 1.0


def test_vector_blank_page_is_locally_confirmed_without_running_ocr(tmp_path: Path) -> None:
    from knowledge_pipeline import pdf_convert

    path = tmp_path / "vector-blank.pdf"
    _pdf(path)
    ocr = FakeOcr("must not run")

    result = pdf_convert.convert_pdf(
        path,
        ocr=ocr,
        options=pdf_convert.PdfConversionOptions(
            use_docling=False,
            use_camelot=False,
            use_pdfplumber=False,
        ),
    )

    assert ocr.calls == 0
    assert result.status.status == "ready"
    assert result.pages[0].page_type == "scan_noise_only"
    assert result.pages[0].routing_type == "scan_noise_only"
    assert result.pages[0].needs_review is False
    assert result.quality_report.scan_noise_only_pages == [1]
    assert result.quality_report.failed_pages == []
    assert result.quality_report.effective_content_pages == 0


def test_successful_scanned_page_is_not_automatically_low_quality(tmp_path: Path) -> None:
    from knowledge_pipeline import pdf_convert

    path = tmp_path / "scan.pdf"
    image = np.full((800, 600, 3), 255, dtype=np.uint8)
    image[80:120, 100:500] = 0
    _raster_pdf(path, image)
    options = pdf_convert.PdfConversionOptions(use_docling=False, use_camelot=False, use_pdfplumber=False)
    result = pdf_convert.convert_pdf(path, ocr=FakeOcr("扫描页面的有效 OCR 文本"), options=options)

    assert result.pages[0].page_type == "scanned"
    assert result.pages[0].needs_review is False
    assert result.quality_report.low_quality_pages == []
    assert result.quality_report.successfully_recovered_ocr_pages == [1]
    assert result.quality_report.quality_score == 1.0


def test_quality_score_excludes_confirmed_blank_pages_from_page_denominator() -> None:
    from knowledge_pipeline.contracts import QualityReport
    from knowledge_pipeline.pdf_convert import _finalize_quality_report

    report = QualityReport(page_count=200, scan_noise_only_pages=[14])

    _finalize_quality_report(report)

    assert report.effective_content_pages == 199
    assert report.quality_score == 1.0
    assert report.needs_review is False


def test_quality_score_reports_low_confidence_ocr_without_equating_missing_confidence_to_failure() -> None:
    from knowledge_pipeline.contracts import QualityReport
    from knowledge_pipeline.pdf_convert import _quality_score

    report = QualityReport(
        page_count=10,
        effective_content_pages=10,
        text_metrics={
            "ocr_confidence_observation_count": 100,
            "low_confidence_ocr_block_count": 20,
            "low_confidence_ocr_block_ratio": 0.2,
            "ocr_confidence_coverage_ratio": 0.1,
        },
    )

    assert _quality_score(report) == 0.98


def test_unrecovered_ocr_page_is_failed_once(tmp_path: Path, monkeypatch) -> None:
    from knowledge_pipeline import pdf_convert

    path = tmp_path / "unrecovered.pdf"
    _pdf(path)
    monkeypatch.setattr(pdf_convert, "_render_page", lambda *_args: object())

    options = pdf_convert.PdfConversionOptions(use_docling=False, use_camelot=False, use_pdfplumber=False)
    result = pdf_convert.convert_pdf(path, ocr=FakeOcr(""), options=options)

    assert result.status.status == "failed"
    assert result.quality_report.failed_pages == [1]
    assert result.quality_report.low_quality_pages == [1]
    assert result.pages[0].needs_review is True
    assert result.pages[0].warnings.count("页面没有可用原生文本或 OCR 文本") == 1


def test_remote_blank_review_skips_ocr_and_chunks_for_confirmed_blank_page(tmp_path: Path, monkeypatch) -> None:
    from knowledge_pipeline import pdf_convert

    path = tmp_path / "blank.pdf"
    _pdf(path)
    monkeypatch.setattr(pdf_convert, "_render_page", lambda *_args: object())
    vision = FakeBlankVision({
        "classification": "blank", "confidence": 0.99,
        "has_readable_text": False, "has_graphics": False,
        "has_table": False, "has_stamp_or_annotation": False,
        "reason": "页面为空白",
    })
    ocr = FakeOcr("OCR must not run")
    options = pdf_convert.PdfConversionOptions(
        use_docling=False, use_camelot=False, use_pdfplumber=False,
        remote_blank_review=True,
        vision=pdf_convert.PdfVisionOptions(adapter=vision, model="gui-model"),
    )

    result = pdf_convert.convert_pdf(path, ocr=ocr, options=options)

    assert vision.calls == 1
    assert ocr.calls == 0
    assert result.pages[0].page_type == "intentional_blank"
    assert result.pages[0].extraction_method == "remote:gui-responses-blank-review"
    assert result.pages[0].blank_review["classification"] == "blank"
    assert result.quality_report.intentional_blank_pages == [1]
    assert result.quality_report.failed_pages == []
    assert result.blocks == []


def test_remote_blank_review_non_blank_falls_back_to_ocr(tmp_path: Path, monkeypatch) -> None:
    from knowledge_pipeline import pdf_convert

    path = tmp_path / "not-blank.pdf"
    _pdf(path)
    monkeypatch.setattr(pdf_convert, "_render_page", lambda *_args: object())
    vision = FakeBlankVision({
        "classification": "non_blank", "confidence": 0.99,
        "has_readable_text": True, "has_graphics": False,
        "has_table": False, "has_stamp_or_annotation": False,
        "reason": "存在文字",
    })
    ocr = FakeOcr("扫描页有效文本")
    options = pdf_convert.PdfConversionOptions(
        use_docling=False, use_camelot=False, use_pdfplumber=False,
        remote_blank_review=True,
        vision=pdf_convert.PdfVisionOptions(adapter=vision),
    )

    result = pdf_convert.convert_pdf(path, ocr=ocr, options=options)

    assert vision.calls == 1
    assert ocr.calls == 1
    assert result.quality_report.intentional_blank_pages == []
    assert result.pages[0].page_type == "scanned"
    assert result.blocks[0].text == "扫描页有效文本"


def test_remote_blank_review_invalid_response_fails_closed_to_ocr(tmp_path: Path, monkeypatch) -> None:
    from knowledge_pipeline import pdf_convert

    path = tmp_path / "invalid-review.pdf"
    _pdf(path)
    monkeypatch.setattr(pdf_convert, "_render_page", lambda *_args: object())
    vision = FakeBlankVision({"classification": "blank"})
    ocr = FakeOcr("回退 OCR 文本")
    options = pdf_convert.PdfConversionOptions(
        use_docling=False, use_camelot=False, use_pdfplumber=False,
        remote_blank_review=True,
        vision=pdf_convert.PdfVisionOptions(adapter=vision),
    )

    result = pdf_convert.convert_pdf(path, ocr=ocr, options=options)

    assert vision.calls == 1
    assert ocr.calls == 1
    assert result.quality_report.intentional_blank_pages == []
    assert result.pages[0].page_type == "scanned"
    assert any("空白页远程判定失败" in warning for warning in result.pages[0].warnings)


def test_optional_backend_missing_is_non_blocking(tmp_path: Path) -> None:
    from knowledge_pipeline.pdf_convert import PdfConversionOptions, convert_pdf

    path = tmp_path / "native.pdf"
    _pdf(path, "native text for optional backend fallback")
    result = convert_pdf(path, options=PdfConversionOptions(use_docling=True, use_camelot=True, use_pdfplumber=True))

    assert result.blocks
    assert set(result.quality_report.optional_backends) >= {"docling", "camelot", "pdfplumber"}


def test_cancellation_returns_auditable_cancelled_result(tmp_path: Path) -> None:
    from knowledge_pipeline.pdf_convert import convert_pdf

    path = tmp_path / "cancel.pdf"
    _pdf(path, "native text")
    result = convert_pdf(path, should_stop=lambda: True)

    assert result.status.status == "cancelled"
    assert result.status.error


def test_docling_projection_is_page_aware_and_scanned_pages_skip_tables(tmp_path: Path, monkeypatch) -> None:
    from knowledge_pipeline import pdf_convert
    from knowledge_pipeline.contracts import TableRecord

    path = tmp_path / "native.pdf"
    _pdf(path, "native text for docling")
    monkeypatch.setattr(pdf_convert, "_try_docling", lambda *_args, **_kwargs: ("projection", {1: [pdf_convert.BlockRecord(block_id="", location="", text="docling page", page_number=1, extraction_method="layout:docling")]}, [TableRecord(table_id="d", page_number=1, extraction_method="table:docling")], None))
    called: list[set[int]] = []
    monkeypatch.setattr(pdf_convert, "_try_optional_tables", lambda *_args, **kwargs: (called.append(kwargs.get("page_numbers", set())) or ([], [])))
    result = pdf_convert.convert_pdf(path)
    assert result.metadata["backend"] == "docling+pymupdf"
    assert any(table.extraction_method == "table:docling" for table in result.tables)
    assert [block.extraction_method for block in result.blocks].count("native:pymupdf") == 0
    assert any(block.extraction_method == "layout:docling" for block in result.blocks)
    assert called == [set()]
