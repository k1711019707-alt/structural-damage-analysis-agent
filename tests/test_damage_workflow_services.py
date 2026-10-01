from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace


def _confirmed_report(*, damage_type: str = "Structural crack", level: str = "medium") -> dict:
    return {
        "report_schema_version": "damage-report.v3",
        "subject": {
            "project_name": "測試工程",
            "asset_id": "B-01",
            "component": "梁",
            "inspection_time": None,
            "project_overview": "混凝土梁巡檢",
        },
        "executive_summary": "已形成人工確認的損傷報告。",
        "overall_screening_level": level,
        "overall_level_reason": "依可見證據判定。",
        "findings": [{
            "finding_index": 0,
            "image_name": "crack.jpg",
            "damage_type": damage_type,
            "damage_level": level,
            "level_reason": "可見連續損傷，仍需現場複核。",
            "standards_basis": [{"id": "GB 50292-2015", "name": "建築物鑑定標準", "role": "判定參考"}],
            "visual_basis": ["原圖及識別覆蓋圖位置一致"],
            "uncertainty": "未提供可靠物理尺度。",
            "observed_evidence": "表面可見損傷。",
            "risk_interpretation": "需由工程師現場核對。",
            "recommended_action": "先完成現場複核。",
            "confidence_note": "視覺判斷不等於結構安全結論。",
        }],
        "limitations": ["缺少現場量測。"],
        "review_status": "confirmed_by_human",
        "human_review": {
            "status": "confirmed_by_human",
            "reviewer": "測試工程師",
            "reviewed_at": "2026-09-20T01:00:00+00:00",
            "notes": "已核對",
        },
        "integrity": {"correspondence_valid": True, "expected_count": 1, "actual_count": 1, "issues": []},
        "provenance": {
            "model": "report-model",
            "generated_at": "2026-09-20T00:00:00+00:00",
            "source_summary_path": "batch_summary.json",
            "evidence_count": 1,
            "report_schema_version": "damage-report.v3",
            "human_review_required": True,
        },
    }


def _image_results() -> list[dict]:
    return [{
        "image_name": "crack.jpg",
        "image_path": "/tmp/crack.jpg",
        "overlay_path": "/tmp/crack_overlay.jpg",
        "status": "success",
        "damage_findings": [{
            "index": 0,
            "class_name": "Structural crack",
            "area": {"area_ratio": 0.92},
            "screening_severity": {"level": "low"},
        }],
    }]


def test_repair_planner_uses_confirmed_report_and_scale_free_method_card(tmp_path: Path) -> None:
    from runtime.damage_repair_plan import REPAIR_PLAN_VERSION, DamageRepairPlanner

    summary = DamageRepairPlanner().build_summary(
        _image_results(), report=_confirmed_report(level="medium")
    )
    line = summary["lines"][0]

    assert summary["repair_plan_version"] == REPAIR_PLAN_VERSION
    assert summary["source"] == "confirmed_damage_report"
    assert summary["evidence_consistency_status"] == "consistent"
    assert line["repair_item_id"] == "crack.jpg#0"
    assert line["method_id"] == ""
    assert line["repair_method"] == ""
    assert line["severity_level"] == "medium"
    assert line["component_area_ratio"] is None
    assert line["physical_area_mm2"] is None
    assert line["original_image_path"].endswith("crack.jpg")
    assert line["annotated_image_path"].endswith("crack_overlay.jpg")
    assert line["report_damage_level_reason"]
    assert line["report_standards_basis"]
    serialized = json.dumps(summary, ensure_ascii=False).casefold()
    assert "0.92" not in serialized
    assert "rc-c02" not in serialized
    assert "rc-u01" not in serialized
    assert not any(token in serialized for token in {"cost", "price", "currency", "造价", "金额"})
    planner = DamageRepairPlanner()
    planner.save_summary(summary, tmp_path / "repair_plan.json")
    assert json.loads((tmp_path / "repair_plan.json").read_text(encoding="utf-8"))["lines"]


def test_repair_planner_routes_unknown_and_high_risk_findings_to_hold() -> None:
    from runtime.damage_repair_plan import DamageRepairPlanner

    unknown = DamageRepairPlanner().build_summary(
        _image_results(), report=_confirmed_report(damage_type="未知損傷", level="medium")
    )["lines"][0]
    high_risk = DamageRepairPlanner().build_summary(
        _image_results(), report=_confirmed_report(level="high")
    )["lines"][0]

    assert unknown["method_id"] == ""
    assert unknown["decision_status"] == "hold"
    assert high_risk["method_id"] == ""
    assert high_risk["decision_status"] == "hold"


def test_repair_planner_rejects_unconfirmed_report_and_has_no_detection_only_path() -> None:
    import pytest
    from runtime.damage_repair_plan import DamageRepairPlanner

    report = _confirmed_report()
    report["review_status"] = "pending_human_review"
    with pytest.raises(ValueError, match="尚未人工确认"):
        DamageRepairPlanner().build_summary(_image_results(), report=report)

    with pytest.raises(TypeError):
        DamageRepairPlanner().build_summary(_image_results())


def test_repair_planner_builds_image_specific_render_selection() -> None:
    from runtime.damage_repair_plan import DamageRepairPlanner

    results = [
        *_image_results(),
        {"image_name": "clean.jpg", "status": "no_detection", "damage_findings": []},
        {"image_name": "broken.jpg", "status": "failed", "damage_findings": []},
    ]
    planner = DamageRepairPlanner()
    summary = planner.build_summary(results, report=_confirmed_report())
    selection = planner.build_targeted_render_selection(results, summary["lines"])

    assert selection["render_count"] == 0
    assert selection["skipped_no_detection"] == 1
    assert selection["skipped_failed"] == 1
    assert selection["items"] == []


def test_new_repair_plan_contains_only_facts_and_ai_input_slots() -> None:
    from runtime.damage_repair_plan import DamageRepairPlanner

    summary = DamageRepairPlanner().build_summary(
        _image_results(), report=_confirmed_report(level="medium")
    )
    line = summary["lines"][0]
    assert line["method_id"] == ""
    assert line["method_name"] == ""
    assert line["method_display_name"] == ""
    assert line["repair_method"] == ""
    assert line["required_site_verification"] == []
    assert line["upgrade_conditions"] == []
    assert line["stop_work_conditions"] == []


def test_render_selection_uses_reviewed_method_without_control_card_prefix() -> None:
    from runtime.damage_repair_plan import DamageRepairPlanner

    selection = DamageRepairPlanner.build_targeted_render_selection(
        _image_results(),
        [{
            "image_name": "crack.jpg",
            "method_id": "RC-U01",
            "method_display_name": "工程师确认的裂缝修复",
            "repair_method": "现场复核后清理裂缝并按批准方案灌注修复",
            "reviewed_method": True,
        }],
    )

    assert selection["render_count"] == 1
    assert selection["items"][0][1] == (
        "工程师确认的裂缝修复：现场复核后清理裂缝并按批准方案灌注修复"
    )
    assert "RC-U01" not in selection["items"][0][1]


def test_reviewed_render_selection_does_not_skip_missing_detection_metadata() -> None:
    from runtime.damage_repair_plan import DamageRepairPlanner

    selection = DamageRepairPlanner.build_reviewed_render_selection(
        [{"image_name": "other-name.jpg", "status": "failed", "damage_findings": []}],
        [{
            "image_name": "crack.jpg",
            "original_image_path": "E:/input/crack.jpg",
            "method_display_name": "工程师确认的裂缝修复",
            "repair_method": "现场复核后按批准方案灌注修复",
        }],
    )

    assert selection["render_count"] == 1
    assert selection["items"] == [
        ("E:/input/crack.jpg", "工程师确认的裂缝修复：现场复核后按批准方案灌注修复")
    ]
    assert selection["skipped_no_detection"] == 0
    assert selection["skipped_failed"] == 0


def test_reviewed_render_selection_uses_detection_path_only_as_fallback() -> None:
    from runtime.damage_repair_plan import DamageRepairPlanner

    selection = DamageRepairPlanner.build_reviewed_render_selection(
        [{
            "image_name": "crack.jpg",
            "image_path": "E:/input/crack.jpg",
            "status": "no_detection",
            "damage_findings": [],
        }],
        [{
            "image_name": "crack.jpg",
            "method_display_name": "审核工法",
            "repair_method": "按审核方案实施",
        }],
    )

    assert selection["render_count"] == 1
    assert selection["items"][0][0] == "E:/input/crack.jpg"


def test_fhl_renderer_resumes_existing_output(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    output_dir = tmp_path / "renders"
    output_dir.mkdir()
    output = output_dir / "damage__修復渲染.jpg"
    output.write_bytes(b"render")
    (output_dir / "render_manifest.json").write_text(
        json.dumps({"renders": [{"source_path": str(source), "status": "success"}]}),
        encoding="utf-8",
    )

    def fail_runner(*_args, **_kwargs):
        raise AssertionError("resume should not call FHL")

    result = FhlRepairRenderer(output_dir=output_dir, script_path=tmp_path / "missing.mjs", runner=fail_runner).render_one(source, repair_method="修復")
    assert result.status == "resumed"
    assert result.output_path == str(output)


def test_fhl_renderer_records_failure_without_overwriting_source(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    script = tmp_path / "generate.mjs"
    script.write_text("// test", encoding="utf-8")

    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="[1/1] FAILED attempts=4 HTTP 502: Bad gateway\n",
            stderr="Edit failed: HTTP 502: Bad gateway retry_after=60s\n",
        )

    renderer = FhlRepairRenderer(
        output_dir=tmp_path / "renders",
        script_path=script,
        runner=runner,
    )
    result = renderer.render_one(source, repair_method="修復")
    assert result.status == "failed"
    assert "HTTP 502" in result.message
    assert "exit 1" in result.message
    assert source.read_bytes() == b"source"
    manifest = json.loads((tmp_path / "renders" / "render_manifest.json").read_text(encoding="utf-8"))
    assert manifest["renders"][0]["status"] == "failed"
    assert "HTTP 502" in manifest["renders"][0]["message"]


def test_fhl_renderer_falls_back_to_return_code_for_plugin_failures(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    script = tmp_path / "generate.mjs"
    script.write_text("// test", encoding="utf-8")

    def failed_runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=7,
            stdout="",
            stderr="ERROR: authorization failed",
        )

    result = FhlRepairRenderer(
        output_dir=tmp_path / "renders",
        script_path=script,
        runner=failed_runner,
    ).render_one(source, repair_method="修復")

    assert "exit 7" in result.message
    assert "authorization failed" in result.message

    def silent_runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=9, stdout="", stderr="")

    silent = FhlRepairRenderer(
        output_dir=tmp_path / "silent-renders",
        script_path=script,
        runner=silent_runner,
    ).render_one(source, repair_method="修復")
    assert silent.message == "FHL Images API 未成功回傳（exit 9）"


def test_fhl_renderer_legacy_plugin_fallback_receives_no_gui_provider_secrets(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    script = tmp_path / "generate.mjs"
    script.write_text("// test", encoding="utf-8")
    captured = {}

    def runner(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=1, stdout="", stderr="failed")

    renderer = FhlRepairRenderer(
        output_dir=tmp_path / "renders",
        script_path=script,
        api_url="https://example.test/v1/images/edits",
        runner=runner,
    )
    renderer.render_one(source, repair_method="修復")
    assert "FHL_API_KEY" not in captured["env"]
    assert "APIMART_API_KEY" not in captured["env"]


def test_fhl_renderer_routes_plugin_output_to_selected_directory(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    script = tmp_path / "generate.mjs"
    script.write_text("// test", encoding="utf-8")
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=1, stdout="", stderr="failed")

    output_dir = tmp_path / "输入目录" / "構件視界_分析輸出" / "修復渲染"
    FhlRepairRenderer(output_dir=output_dir, script_path=script, runner=runner).render_one(
        source, repair_method="修復"
    )
    assert "--output-dir" in captured["command"]
    output_index = captured["command"].index("--output-dir") + 1
    assert Path(captured["command"][output_index]) == output_dir.resolve()
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    if os.name == "nt":
        assert captured["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def test_fhl_renderer_copies_and_cleans_plugin_image_in_selected_directory(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    source = tmp_path / "damage.jpg"
    source.write_bytes(b"source")
    script = tmp_path / "generate.mjs"
    script.write_text("// test", encoding="utf-8")
    output_dir = tmp_path / "input" / "構件視界_分析輸出" / "修復渲染"

    def runner(*_args, **_kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "edit_result.png").write_bytes(b"render")
        return SimpleNamespace(returncode=0, stdout="完成", stderr="")

    result = FhlRepairRenderer(output_dir=output_dir, script_path=script, runner=runner).render_one(
        source, repair_method="修復"
    )
    assert result.status == "success"
    assert Path(result.output_path).read_bytes() == b"render"
    assert not (output_dir / "edit_result.png").exists()


def test_fhl_renderer_stops_remaining_items_before_starting_them(tmp_path: Path) -> None:
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    script = tmp_path / "generate.mjs"
    script.write_text("// test", encoding="utf-8")
    one = tmp_path / "one.jpg"
    two = tmp_path / "two.jpg"
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    calls = []

    def runner(*_args, **_kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=1, stdout="", stderr="failed")

    stop = {"value": False}
    results = FhlRepairRenderer(
        output_dir=tmp_path / "renders", script_path=script, runner=runner
    ).render_many(
        [(one, "方案一"), (two, "方案二")],
        should_stop=lambda: stop["value"],
    )
    assert len(calls) == 2
    stop["value"] = True
    results = FhlRepairRenderer(
        output_dir=tmp_path / "renders2", script_path=script, runner=runner
    ).render_many(
        [(one, "方案一"), (two, "方案二")],
        should_stop=lambda: stop["value"],
    )
    assert all(result.status == "cancelled" for result in results)


def test_fhl_console_suppression_patch_keeps_generate_script_valid(tmp_path: Path) -> None:
    module_path = Path(__file__).parents[1] / "packaging" / "ensure_fhl_console_hidden.py"
    spec = importlib.util.spec_from_file_location("ensure_fhl_console_hidden", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    patch_script = module.patch_script

    source = Path(r"C:\Users\17110\.codex\plugins\cache\fhl-plugins\fhl-image-gen\0.2.1\scripts\generate.mjs")
    if not source.is_file():
        return
    target = tmp_path / "generate.mjs"
    shutil.copy2(source, target)
    patch_script(target)
    node = Path(r"D:\Program Files\nodejs\node.exe")
    if not node.is_file():
        return
    checked = subprocess.run(
        [str(node), "--check", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
