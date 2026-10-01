from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


def _write_image(path: Path, size: tuple[int, int] = (32, 24)) -> None:
    Image.new("RGB", size, (120, 90, 70)).save(path)


def _summary(tmp_path: Path, *, count: int = 1) -> tuple[dict, Path]:
    image = tmp_path / "beam.png"
    overlay = tmp_path / "beam-overlay.png"
    _write_image(image)
    _write_image(overlay)
    summary = {
        "project_overview": "桥梁底部巡检",
        "model_provenance": {"name": "local-yolo"},
        "results": [
            {
                "image_name": image.name,
                "image_path": str(image),
                "overlay_path": str(overlay),
                "status": "success",
                "damage_findings": [
                    {
                        "index": index,
                        "class_id": 6,
                        "class_name": "Structural crack",
                        "score": 0.91,
                        "detection_confidence": 0.91,
                        "box": [1, 2, 3, 4],
                        "area": {"area_pixels": 1234, "area_ratio": 0.42},
                        "crack_geometry": {"maximum_width_px": 9.9},
                        "screening_severity": {"level": "critical"},
                    }
                    for index in range(count)
                ],
            }
        ],
    }
    summary_path = tmp_path / "batch_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return summary, summary_path


def _finding(index: int = 0, *, level: str = "medium") -> dict:
    return {
        "finding_index": index,
        "image_name": "beam.png",
        "damage_type": "Structural crack",
        "damage_level": level,
        "level_reason": "可见连续裂缝，需现场复核。",
        "standards_basis": [],
        "visual_basis": ["原图显示连续线性裂缝"],
        "uncertainty": "缺少现场量测。",
        "observed_evidence": "原图和识别覆盖图均显示连续裂缝。",
        "risk_interpretation": "可能影响耐久性。",
        "recommended_action": "由专业人员现场复核。",
        "confidence_note": "AI 辅助判断。",
    }


def _draft(*, count: int = 1, level: str = "medium", identities: list[int] | None = None) -> str:
    indexes = identities if identities is not None else list(range(count))
    return json.dumps(
        {
            "subject": {
                "project_name": None,
                "asset_id": None,
                "component": None,
                "inspection_time": None,
                "project_overview": "桥梁底部巡检",
            },
            "executive_summary": "发现疑似裂缝，等待人工复核。",
            "overall_screening_level": level,
            "overall_level_reason": "依据可见损伤形态进行辅助判断。",
            "findings": [_finding(index, level=level) for index in indexes],
            "limitations": ["未使用像素或物理尺寸判级。"],
        },
        ensure_ascii=False,
    )


class _Responses:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.outputs.pop(0))


def _service(outputs: list[str], **kwargs):
    from runtime.responses_damage_report import ResponsesReportService

    responses = _Responses(outputs)
    service = ResponsesReportService(
        api_key="test-key",
        model="vision-test",
        client=SimpleNamespace(responses=responses),
        **kwargs,
    )
    return service, responses


def test_v3_request_excludes_geometry_and_prepares_visual_audit(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageReportRequest
    from runtime.responses_damage_report import prepare_visual_evidence

    summary, summary_path = _summary(tmp_path)
    request = DamageReportRequest.from_summary(summary, str(summary_path))
    payload = json.dumps(request.prompt_payload(), ensure_ascii=False)

    assert "area_pixels" not in payload
    assert "area_ratio" not in payload
    assert "maximum_width_px" not in payload
    assert "screening_severity" not in payload
    prepared = prepare_visual_evidence(request)
    assert [item.role for item in prepared] == ["original_image", "recognition_overlay"]
    assert all(item.data_url.startswith("data:image/jpeg;base64,") for item in prepared)
    assert all("data_url" not in item.audit() for item in prepared)


def test_remote_v3_uses_images_and_local_system_fields(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageReportRequest, REPORT_SCHEMA_VERSION

    summary, summary_path = _summary(tmp_path)
    request = DamageReportRequest.from_summary(summary, str(summary_path))
    service, responses = _service([_draft()])

    report = service.generate_report(request)

    assert report.report_schema_version == REPORT_SCHEMA_VERSION
    assert report.review_status == "pending_human_review"
    assert report.human_review.reviewer == ""
    assert report.provenance.model == "vision-test"
    assert report.provenance.source_summary_path == str(summary_path)
    content = responses.calls[0]["input"][0]["content"]
    assert content[0]["type"] == "input_text"
    assert [item["type"] for item in content[1:]] == ["input_image", "input_image"]


@pytest.mark.parametrize("identities", [[0, 0], []])
def test_remote_v3_blocks_duplicate_or_missing_identity(tmp_path: Path, identities: list[int]) -> None:
    from runtime.damage_report_schema import DamageReportRequest
    from runtime.responses_damage_report import ReportGenerationError

    summary, summary_path = _summary(tmp_path, count=2)
    request = DamageReportRequest.from_summary(summary, str(summary_path))
    service, _ = _service([_draft(identities=identities)])

    with pytest.raises(ReportGenerationError, match="correspondence mismatch"):
        service.generate_report(request)


def test_damage_level_aliases_are_normalized_and_unknown_rejected() -> None:
    from pydantic import ValidationError
    from runtime.damage_report_schema import ReportFinding

    finding = ReportFinding.model_validate(_finding(level="高"))
    assert finding.damage_level == "high"
    with pytest.raises(ValidationError):
        ReportFinding.model_validate(_finding(level="一级"))


def test_chunked_v3_recomputes_overall_and_preserves_input_order(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageReportRequest

    summary, summary_path = _summary(tmp_path, count=9)
    request = DamageReportRequest.from_summary(summary, str(summary_path))
    outputs = [
        json.dumps(
            {"findings": [_finding(index, level="高" if index == 8 else "中")]},
            ensure_ascii=False,
        )
        for index in range(9)
    ]
    service, responses = _service(outputs)

    report = service.generate_report(request)

    assert [item.finding_index for item in report.findings] == list(range(9))
    assert report.overall_screening_level == "high"
    assert len(responses.calls) == 9


def test_local_fallback_never_assigns_damage_level(tmp_path: Path) -> None:
    from runtime.responses_damage_report import build_local_fallback_report_from_summary

    _, summary_path = _summary(tmp_path, count=2)
    report = build_local_fallback_report_from_summary(
        summary_path,
        reason="HTTP 503 sk-sensitive-value-123",
    )

    assert report.overall_screening_level == "undetermined"
    assert {item.damage_level for item in report.findings} == {"undetermined"}
    persisted = (tmp_path / "report.json").read_text(encoding="utf-8")
    assert "sk-sensitive-value-123" not in persisted
    assert "面积占比" not in persisted


def test_confirmation_requires_real_reviewer_and_determinate_findings(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageReportRequest
    from runtime.responses_damage_report import save_human_reviewed_report

    summary, summary_path = _summary(tmp_path)
    request = DamageReportRequest.from_summary(summary, str(summary_path))
    service, _ = _service([_draft()])
    report = service.generate_report_from_summary(summary_path)

    with pytest.raises(ValueError, match="复核人"):
        save_human_reviewed_report(tmp_path / "report.json", report, confirmed=True)
    report.human_review.reviewer = "张工"
    saved = save_human_reviewed_report(tmp_path / "report.json", report, confirmed=True)
    assert saved.review_status == "confirmed_by_human"
    assert saved.human_review.reviewed_at

    report.findings[0].damage_level = "undetermined"
    report.human_review.reviewer = "张工"
    with pytest.raises(ValueError, match="待判定"):
        from runtime.damage_report_schema import validate_report_for_confirmation
        validate_report_for_confirmation(report, expected_identities=request.identities())


def test_report_citations_keep_filename_and_marker_after_review_save(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageReportRequest, StandardReference
    from runtime.generation_context import GenerationContext
    from runtime.responses_damage_report import save_human_reviewed_report

    marker = "[KB:00474f7b00ed539d4702:page:42]"
    summary, summary_path = _summary(tmp_path)
    request = DamageReportRequest.from_summary(summary, str(summary_path))
    service, _ = _service([_draft()])
    context = GenerationContext(
        profile_name="分析报告",
        profile_version=3,
        settings_snapshot_id="snapshot-citations",
        evidence={},
        knowledge_base_used=True,
        retrieved_chunks=[{
            "chunk_id": "chunk-42",
            "document_id": "00474f7b00ed539d4702",
            "location": "unit:171",
            "source_marker": marker,
            "score": 0.9,
            "metadata": {
                "source_name": (
                    "C:\\private\\source_files\\"
                    "GB 50010-2010 混凝土结构设计规范-上.pdf"
                )
            },
            "text": "结构分析应符合实际工作状况。",
        }],
        knowledge_base_status="used",
    )
    report = service.generate_report_from_summary(
        summary_path,
        output_dir=tmp_path,
        generation_context=context,
    )
    report.findings[0].standards_basis = [
        StandardReference(
            id="GB 50010-2010",
            name="混凝土结构设计规范",
            role=f"结构分析复核依据。{marker}",
        )
    ]
    report.human_review.reviewer = "张工"
    save_human_reviewed_report(tmp_path / "report.json", report, confirmed=True)

    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert payload["citation_catalog"] == [{
        "source_marker": marker,
        "document_id": "00474f7b00ed539d4702",
        "source_name": "GB 50010-2010 混凝土结构设计规范-上.pdf",
        "location": "page:42",
    }]
    assert "来源：GB 50010-2010 混凝土结构设计规范-上.pdf，第42页" in markdown
    assert marker in markdown
    assert r"C:\private" not in markdown


def test_visual_attachment_count_budget_blocks_before_network(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageReportRequest
    from runtime.responses_damage_report import ReportGenerationError, VisualBudget

    summary, summary_path = _summary(tmp_path)
    request = DamageReportRequest.from_summary(summary, str(summary_path))
    service, responses = _service([_draft()], visual_budget=VisualBudget(max_attachments=1))

    with pytest.raises(ReportGenerationError, match="数量超过预算"):
        service.generate_report(request)
    assert responses.calls == []


def test_active_project_code_has_no_external_optimization_dependency() -> None:
    root = Path(__file__).parents[1]
    targets = [root / name for name in ("runtime", "scripts", "configs", "packaging")]
    targets.append(root / "start_yolo11s_seg_gui.bat")
    matches: list[str] = []
    for target in targets:
        paths = [target] if target.is_file() else target.rglob("*") if target.is_dir() else []
        for path in paths:
            if not path.is_file() or path.suffix.lower() in {
                ".pyc", ".png", ".jpg", ".jpeg", ".docx", ".pdf",
            }:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            normalized = content.replace("/", "\\")
            if "E:\\桌面\\海之子\\分析报告生成" in normalized:
                matches.append(str(path.relative_to(root)))
    assert matches == []


def test_report_manifest_preserves_active_v2_rag_diagnostics(tmp_path: Path) -> None:
    from runtime.generation_context import GenerationContext

    _, summary_path = _summary(tmp_path)
    context = GenerationContext(
        profile_name="損傷分析報告",
        profile_version=3,
        settings_snapshot_id="snapshot-v2",
        evidence={"results": []},
        knowledge_base_used=True,
        retrieved_chunks=[{
            "chunk_id": "c1",
            "document_id": "doc-v2",
            "location": "page:1",
            "source_marker": "[KB:doc-v2:page:1]",
            "score": 0.9,
            "metadata": {"retrieval_role": "retrieval"},
            "text": "只进入模型上下文，不进入清单。",
        }],
        prompt="按证据生成报告。",
        profile_prompt="按证据生成报告。",
        knowledge_base_retrieval_mode="hybrid_semantic",
        knowledge_base_status="used",
        knowledge_base_scope_document_ids=("doc-v2",),
        anchors=[{"chunk_id": "c1"}],
        context_groups=[{"anchor_chunk_ids": ["c1"], "chunk_ids": ["c1", "p1"]}],
        retrieval_warnings=["semantic sidecar fallback model"],
        answer_source_mode="knowledge_base",
        route_diagnostics={"routing_mode": "adaptive"},
    )
    service, _ = _service([_draft()])

    service.generate_report_from_summary(summary_path, generation_context=context)

    manifest = json.loads((tmp_path / "generation_manifest.json").read_text(encoding="utf-8"))
    profile = manifest["profiles"]["損傷分析報告"]
    assert profile["knowledge_base_retrieval_mode"] == "hybrid_semantic"
    assert profile["knowledge_base_scope_document_ids"] == ["doc-v2"]
    assert profile["anchors"] == [{"chunk_id": "c1"}]
    assert profile["context_groups"][0]["anchor_chunk_ids"] == ["c1"]
    assert profile["retrieval_warnings"] == ["semantic sidecar fallback model"]
    assert profile["answer_source_mode"] == "knowledge_base"
    assert profile["route_diagnostics"] == {"routing_mode": "adaptive"}
    assert "text" not in profile["retrieved_chunks"][0]
