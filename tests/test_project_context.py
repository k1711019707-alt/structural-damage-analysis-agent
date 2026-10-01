from __future__ import annotations

from runtime.project_context import analyze_project_overview


def test_empty_project_overview_has_no_context_controls() -> None:
    controls = analyze_project_overview("")

    assert controls.report_notes == []
    assert controls.preconstruction_checks == []
    assert controls.safety_controls == []
    assert controls.limitations == []


def test_explicit_project_terms_create_only_grounded_context_controls() -> None:
    controls = analyze_project_overview(
        "住宅楼地下室包含地下车库和设备机房，涉及防火分区、荷载传递和抗震作用。"
    )
    combined = " ".join(
        controls.report_notes
        + controls.preconstruction_checks
        + controls.safety_controls
        + controls.limitations
    )

    for expected in ("地下空间", "车辆", "设备机房", "防火", "荷载传递", "抗震", "居民"):
        assert expected in combined


def test_unmentioned_project_scenarios_are_not_inferred() -> None:
    controls = analyze_project_overview("某办公楼室内梁柱表面巡检。")
    combined = " ".join(
        controls.report_notes
        + controls.preconstruction_checks
        + controls.safety_controls
    )

    for unsupported in ("地下空间", "车辆", "设备机房", "防火分区", "抗震", "居民"):
        assert unsupported not in combined
