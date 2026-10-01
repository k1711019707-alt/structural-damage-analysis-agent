from __future__ import annotations


def _finding(
    name: str,
    *,
    area_pixels: int,
    frame_pixels: int,
    severity: str,
    maximum_width_px: float | None = None,
    length_px: float | None = None,
) -> dict:
    return {
        "class_name": name,
        "detection_confidence": 0.9,
        "area": {
            "area_pixels": area_pixels,
            "measurement_frame_pixels": frame_pixels,
        },
        "crack_geometry": {
            "maximum_width_px": maximum_width_px,
            "length_px": length_px,
        },
        "screening_severity": {"level": severity},
    }


def test_concrete_template_mapping_uses_reviewed_report_and_never_invents_geometry() -> None:
    from runtime.concrete_damage_report_mapping import build_concrete_damage_template_values

    summary = {
        "project_overview": "跨海桥引桥外观巡检",
        "results": [
            {
                "status": "success",
                "damage_findings": [
                    _finding("Microcrack", area_pixels=12, frame_pixels=1000, severity="medium", maximum_width_px=4.5, length_px=65.0),
                    _finding("Rebar corrosion", area_pixels=80, frame_pixels=1000, severity="high"),
                    _finding("Structural crack", area_pixels=25, frame_pixels=1000, severity="medium", maximum_width_px=7.0, length_px=44.0),
                ],
            }
        ],
    }
    report = {
        "report_schema_version": "damage-report.v3",
        "review_status": "confirmed_by_human",
        "human_review": {"status": "confirmed_by_human"},
        "subject": {"project_name": "试验工程", "asset_id": "C-01", "component": "盖梁"},
        "executive_summary": "结构化检测结果已汇总。",
        "overall_screening_level": "high",
        "findings": [
            {"damage_type": "Microcrack", "damage_level": "medium", "recommended_action": "现场复核后确定处置。"},
            {"damage_type": "Rebar corrosion", "damage_level": "high", "recommended_action": "现场复核后确定处置。"},
            {"damage_type": "Structural crack", "damage_level": "medium", "recommended_action": "现场复核后确定处置。"},
        ],
        "limitations": ["未提供标定。"],
    }

    values = build_concrete_damage_template_values(summary, report)

    assert values["project_name"] == "试验工程"
    assert values["project_overview"] == "跨海桥引桥外观巡检"
    assert values["f1n"] == "1"
    assert values["f1w"] == "不适用（无尺度换算）"
    assert values["f2l"] == "不适用（无尺度换算）"
    assert values["f6a"] == "不适用（无尺度换算）"
    assert values["f6g"] == "高（AI辅助判断，已人工确认）"
    assert values["f6x"] == "是"
    assert values["f3n"] == "0"
    assert values["warn"] == "存在"
    assert "不能从像素换算实体工程量" in values["conclusion_text"]
    assert values["area_ratio"] == "不适用（无尺度换算）"


def test_concrete_template_mapping_marks_missing_metadata_without_blanks() -> None:
    from runtime.concrete_damage_report_mapping import build_concrete_damage_template_values

    values = build_concrete_damage_template_values({"results": []}, {"subject": {}})

    for field in ("project_name", "component_id", "project_overview", "inspection_date"):
        assert values[field] == "未提供"
    assert values["image_count_and_view"] == "0 张图像 / 0 个视角（按文件计）"
