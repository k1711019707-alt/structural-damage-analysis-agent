from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


def _plan_payload(*, plan_status: str = "pending_engineer_review") -> dict:
    return {
        "plan_schema_version": "construction-plan.v2",
        "plan_status": plan_status,
        "construction_released": False,
        "project_id": "A-1",
        "report_id": "report:1234",
        "project_overview": "地下室框架柱巡检",
        "expected_evidence_count": 1,
        "actual_evidence_count": 1,
        "evidence_consistency_status": "consistent",
        "evidence_consistency_note": "证据身份逐项一致",
        "scope": "依据已确认报告形成施工草案。",
        "executive_summary": "施工前仍需现场复核。",
        "preconstruction_checks": ["核对损伤身份"],
        "work_items": [{
            "work_item_id": "work-1",
            "repair_item_id": "repair-1",
            "finding_id": "crack.jpg#0",
            "image_name": "crack.jpg",
            "finding_index": 0,
            "original_image_path": "images/crack.jpg",
            "annotated_image_path": "images/crack-overlay.jpg",
            "construction_image_path": None,
            "figures": [{
                "figure_type": "original",
                "path": "images/crack.jpg",
                "caption": "原始损伤图",
                "availability": "available",
            }],
            "damage_type": "Structural crack",
            "damage_level": "medium",
            "report_damage_level_reason": "连续裂缝。",
            "report_standards_basis": [{"id": "GB 50292", "name": "鉴定标准", "role": "复核依据"}],
            "report_visual_basis": ["原图与覆盖图一致"],
            "report_observed_evidence": "可见连续裂缝。",
            "report_risk_interpretation": "需复核活动性。",
            "report_recommended_action": "现场复测。",
            "report_uncertainty": "无物理尺度。",
            "method_id": "RC-C02",
            "method_name": "crack_repair",
            "method_display_name": "裂缝修复",
            "base_repair_method": "封闭处理",
            "decision_status": "review_required",
            "quantity_basis": "按可见范围复核",
            "component_area_ratio": None,
            "physical_area_mm2": None,
            "applicability_conditions": ["裂缝稳定"],
            "required_site_measurements": ["复测宽度"],
            "method_required_site_verification": ["核验活动性"],
            "method_upgrade_conditions": ["裂缝继续发展"],
            "method_code_references": ["GB 50292"],
            "materials": ["相容修复材料"],
            "equipment": ["裂缝测量仪"],
            "procedure_steps": ["清理", "施工"],
            "quality_control_points": ["过程可追溯"],
            "acceptance_checks": ["外观复检"],
            "safety_controls": ["隔离作业区"],
            "stop_work_conditions": ["证据不一致立即停工"],
            "method_excluded_conclusions": ["不得推定承载力"],
            "assumptions": ["工程量待复核"],
            "review_required": True,
            "knowledge_references": ["[规范A 4.2]"],
        }],
        "general_quality_requirements": ["关键工序可追溯"],
        "general_safety_requirements": ["异常时停工"],
        "post_repair_inspection": ["完成复检"],
        "schedule_assumptions": ["工期另行确认"],
        "excluded_items": ["未确认工程量"],
        "limitations": ["不得替代正式设计"],
        "knowledge_references": ["[规范A 4.2]"],
        "provenance": {
            "model": "construction-model",
            "generated_at": "2026-09-20T00:00:00+00:00",
            "source_report_hash": "a" * 64,
            "repair_plan_hash": "b" * 64,
            "settings_snapshot_id": "snapshot-1",
            "human_review_required": True,
        },
    }


def _persist_plan(path: Path, *, plan_status: str = "pending_engineer_review") -> None:
    path.write_text(
        json.dumps({
            "construction_plan": _plan_payload(plan_status=plan_status),
            "repair_plan_snapshot": {"lines": [{"repair_item_id": "repair-1"}]},
            "plan_schema_version": "construction-plan.v2",
            "fallback": True,
            "fallback_reason": "offline",
            "generation_audit": {"generation_mode": "local_fallback", "marker": "preserve-me"},
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_old_construction_plan_loads_as_pending_review(tmp_path: Path) -> None:
    from runtime.responses_construction_plan import load_construction_plan

    path = tmp_path / "construction_plan.json"
    _persist_plan(path)

    plan = load_construction_plan(path)

    assert plan.review_status == "pending_engineer_review"
    assert plan.human_review.status == "pending_engineer_review"
    assert plan.construction_released is False


def test_review_save_preserves_envelope_and_protected_engineering_facts(tmp_path: Path) -> None:
    from runtime.responses_construction_plan import (
        load_construction_plan,
        save_human_reviewed_construction_plan,
    )

    path = tmp_path / "construction_plan.json"
    _persist_plan(path)
    proposed = load_construction_plan(path)
    proposed.executive_summary = "工程师修改后的摘要。"
    proposed.work_items[0].materials = ["复核后的材料说明"]
    proposed.work_items[0].ai_method_name = "审核后的裂缝灌注工法"
    proposed.work_items[0].base_repair_method = "清理裂缝后低压灌注并完成养护复检"
    proposed.work_items[0].method_rationale = "适用于现场确认稳定且满足灌注条件的裂缝"
    proposed.work_items[0].repair_method_source = "remote_ai"
    proposed.work_items[0].method_id = "MALICIOUS"
    proposed.work_items[0].stop_work_conditions = []
    proposed.expected_evidence_count = 99
    proposed.provenance.model = "changed-model"
    proposed.construction_released = True
    proposed.human_review.reviewer = "王工"
    proposed.human_review.notes = "已核对施工说明。"

    reviewed = save_human_reviewed_construction_plan(path, proposed, confirmed=False)
    envelope = json.loads(path.read_text(encoding="utf-8"))

    assert reviewed.review_status == "edited_pending_confirmation"
    assert reviewed.executive_summary == "工程师修改后的摘要。"
    assert reviewed.work_items[0].materials == ["复核后的材料说明"]
    assert reviewed.work_items[0].ai_method_name == "审核后的裂缝灌注工法"
    assert reviewed.work_items[0].base_repair_method == "清理裂缝后低压灌注并完成养护复检"
    assert reviewed.work_items[0].method_rationale == "适用于现场确认稳定且满足灌注条件的裂缝"
    assert reviewed.work_items[0].repair_method_source == "legacy_local"
    assert reviewed.work_items[0].method_id == "RC-C02"
    assert reviewed.work_items[0].stop_work_conditions == ["证据不一致立即停工"]
    assert reviewed.expected_evidence_count == 1
    assert reviewed.provenance.model == "construction-model"
    assert reviewed.construction_released is False
    assert envelope["repair_plan_snapshot"]["lines"][0]["repair_item_id"] == "repair-1"
    assert envelope["fallback"] is True
    assert envelope["generation_audit"]["marker"] == "preserve-me"


@pytest.mark.parametrize("plan_status", ["hold", "evidence_inconsistent"])
def test_confirmation_requires_reviewer_and_keeps_blocking_status(
    plan_status: str, tmp_path: Path
) -> None:
    from runtime.responses_construction_plan import (
        load_construction_plan,
        save_human_reviewed_construction_plan,
    )

    path = tmp_path / "construction_plan.json"
    _persist_plan(path, plan_status=plan_status)
    proposed = load_construction_plan(path)
    with pytest.raises(ValueError, match="必须填写审核人"):
        save_human_reviewed_construction_plan(path, proposed, confirmed=True)

    proposed.human_review.reviewer = "李工"
    proposed.human_review.notes = "高风险方案仅完成审阅。"
    confirmed = save_human_reviewed_construction_plan(path, proposed, confirmed=True)

    assert confirmed.review_status == "confirmed_by_engineer"
    assert confirmed.human_review.status == "confirmed_by_engineer"
    assert confirmed.human_review.reviewer == "李工"
    assert confirmed.human_review.reviewed_at
    assert confirmed.plan_status == plan_status
    assert confirmed.construction_released is False


def test_construction_review_dialog_is_structured_and_confirms_without_release(
    tmp_path: Path,
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.responses_construction_plan import load_construction_plan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "construction_plan.json"
    _persist_plan(path)
    parent = gui.DamageWorkflowWindow()
    dialog = gui.ConstructionPlanReviewDialog(load_construction_plan(path), path, parent)

    assert dialog.isModal()
    assert dialog.windowTitle() == "施工方案人工审核"
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "总体信息", "施工分项（1）", "通用要求", "审核确认", "原始数据",
    ]
    assert dialog.raw_plan_editor.isReadOnly()
    assert "不代表施工放行" in dialog.guidance_label.text()
    dialog.resize(1120, 780)
    dialog.show()
    app.processEvents()
    assert dialog.tabs.geometry().bottom() < dialog.status_label.geometry().top()
    assert dialog.confirm_button.geometry().right() <= dialog.width()
    artifact_dir = os.environ.get("CONSTRUCTION_REVIEW_VISUAL_ARTIFACT_DIR")
    if artifact_dir:
        screenshot = Path(artifact_dir) / "actual-construction-review-dialog-1120x780.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        pixmap = dialog.grab()
        assert not pixmap.isNull()
        assert pixmap.save(str(screenshot), "PNG")
        assert screenshot.stat().st_size > 5_000
        dialog.tabs.setCurrentIndex(1)
        app.processEvents()
        work_items_shot = Path(artifact_dir) / "actual-construction-review-work-items-1120x780.png"
        assert dialog.grab().save(str(work_items_shot), "PNG")
        assert work_items_shot.stat().st_size > 5_000
        dialog.resize(900, 650)
        dialog.tabs.setCurrentIndex(3)
        app.processEvents()
        review_shot = Path(artifact_dir) / "actual-construction-review-confirm-900x650.png"
        assert dialog.grab().save(str(review_shot), "PNG")
        assert review_shot.stat().st_size > 5_000
        assert dialog.confirm_button.geometry().right() <= dialog.width()
    dialog.executive_summary_edit.setPlainText("审核后的施工摘要。")
    dialog.work_item_editors[0]["materials"].setPlainText("审核材料")
    dialog.work_item_editors[0]["ai_method_name"].setPlainText("工程师确认的裂缝修复工法")
    dialog.work_item_editors[0]["base_repair_method"].setPlainText("清理、低压灌注、养护并复检")
    dialog.work_item_editors[0]["method_rationale"].setPlainText("现场确认裂缝稳定后采用")
    dialog.reviewer_edit.setText("赵工")
    dialog.confirm_review()

    assert dialog.result() == QDialog.Accepted
    assert dialog.confirmed_plan is not None
    assert dialog.confirmed_plan.review_status == "confirmed_by_engineer"
    assert dialog.confirmed_plan.construction_released is False
    persisted = json.loads(path.read_text(encoding="utf-8"))["construction_plan"]
    assert persisted["executive_summary"] == "审核后的施工摘要。"
    assert persisted["work_items"][0]["materials"] == ["审核材料"]
    assert persisted["work_items"][0]["ai_method_name"] == "工程师确认的裂缝修复工法"
    assert persisted["work_items"][0]["base_repair_method"] == "清理、低压灌注、养护并复检"
    assert persisted["work_items"][0]["method_rationale"] == "现场确认裂缝稳定后采用"
    assert persisted["work_items"][0]["method_id"] == "RC-C02"
    dialog.deleteLater()
    parent.close()
    app.processEvents()


def test_plan_success_waits_for_review_and_filters_internal_progress(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    plan = ConstructionPlan.model_validate(_plan_payload())
    scheduled: list[bool] = []
    rendered: list[bool] = []
    window._schedule_construction_plan_review_dialog = lambda: scheduled.append(True)
    window.render_repairs = lambda: rendered.append(True)
    window.render_checkbox.setChecked(True)

    window.on_plan_worker_progress(65, "正在请求施工方案：第 1/3 次尝试")
    window.on_plan_worker_progress(70, "远程草稿接收完成，正在本地校验与组装")
    assert "第 1/3 次尝试" not in window.event_log.toPlainText()
    assert "正在本地校验与组装" not in window.event_log.toPlainText()
    assert "正在本地校验与组装" in window.current_generation_status.text()

    window.on_plan_worker_succeeded(plan, "evidence_only", False)

    assert scheduled == [True]
    assert rendered == []
    assert not window.review_construction_plan_button.isHidden()
    assert not window.render_checkbox.isEnabled()
    assert window.start_button.text() == "停止"
    assert window.start_button.objectName() == "stopButton"
    assert not window.start_button.isEnabled()
    log = window.event_log.toPlainText()
    assert "階段完成：修復施工方案" in log
    assert "流程暫停：等待施工方案人工審核" in log
    window.close()
    app.processEvents()


def test_event_log_deduplicates_consecutive_stage_milestones() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()

    window._log_event("階段開始：建立修復施工方案")
    window._log_event("階段開始：建立修復施工方案")

    assert window.event_log.toPlainText().splitlines() == ["階段開始：建立修復施工方案"]
    window.close()
    app.processEvents()


def test_construction_review_confirmation_unlocks_selected_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan
    from runtime.responses_construction_plan import save_human_reviewed_construction_plan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "construction_plan.json"
    _persist_plan(path)
    pending = ConstructionPlan.model_validate(_plan_payload())
    reviewed = pending.model_copy(deep=True)
    reviewed.human_review.reviewer = "周工"
    reviewed = save_human_reviewed_construction_plan(path, reviewed, confirmed=True)

    class AcceptedDialog:
        def __init__(self, *_args, **_kwargs):
            self.confirmed_plan = reviewed
            self.current_plan = reviewed

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(gui, "ConstructionPlanReviewDialog", AcceptedDialog)
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    window.current_construction_plan = pending
    window.render_checkbox.setChecked(True)
    window.render_checkbox.setEnabled(False)
    rendered: list[bool] = []
    window.render_repairs = lambda: rendered.append(True)

    window.open_construction_plan_review_dialog()
    app.processEvents()

    assert window.current_construction_plan.review_status == "confirmed_by_engineer"
    assert window.render_checkbox.isEnabled()
    assert rendered == [True]
    assert "施工方案已由 周工 人工确认" in window.event_log.toPlainText()
    assert "尚未施工放行" in window.status_label.text()
    window.close()
    app.processEvents()


def test_confirmed_no_render_workflow_reaches_100_and_refreshes_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan
    from runtime.responses_construction_plan import save_human_reviewed_construction_plan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "construction_plan.json"
    _persist_plan(path)
    pending = ConstructionPlan.model_validate(_plan_payload())
    reviewed = pending.model_copy(deep=True)
    reviewed.human_review.reviewer = "陈工"
    reviewed.human_review.notes = "已核对，不生成可选渲染。"
    reviewed = save_human_reviewed_construction_plan(path, reviewed, confirmed=True)

    class AcceptedDialog:
        def __init__(self, *_args, **_kwargs):
            self.confirmed_plan = reviewed
            self.current_plan = reviewed

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(gui, "ConstructionPlanReviewDialog", AcceptedDialog)
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    window.current_construction_plan = pending
    window.render_checkbox.setChecked(False)
    window.recognition_progress.setValue(75)
    window._set_primary_button_running(enabled=False)
    window.open_construction_plan_review_dialog()

    assert window.recognition_progress.value() == 100
    assert "confirmed_by_engineer" in window.current_generation_content.toPlainText()
    assert "審核人：陈工" in window.current_generation_content.toPlainText()
    assert window.current_generation_status.text() == "审核完成；流程已结束"
    assert "流程完成：施工方案审核已结束" in window.event_log.toPlainText()
    assert window.start_button.text() == "启动"
    assert window.start_button.isEnabled()
    window.close()
    app.processEvents()


def test_confirmed_hold_workflow_continues_into_checked_preview_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan
    from runtime.responses_construction_plan import save_human_reviewed_construction_plan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "construction_plan.json"
    _persist_plan(path, plan_status="hold")
    pending = ConstructionPlan.model_validate(_plan_payload(plan_status="hold"))
    reviewed = pending.model_copy(deep=True)
    reviewed.human_review.reviewer = "高工"
    reviewed = save_human_reviewed_construction_plan(path, reviewed, confirmed=True)

    class AcceptedDialog:
        def __init__(self, *_args, **_kwargs):
            self.confirmed_plan = reviewed
            self.current_plan = reviewed

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(gui, "ConstructionPlanReviewDialog", AcceptedDialog)
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    window.current_construction_plan = pending
    window._set_primary_button_running(enabled=False)
    window.render_checkbox.setChecked(True)
    rendered: list[bool] = []
    window.render_repairs = lambda: rendered.append(True)

    window.open_construction_plan_review_dialog()
    app.processEvents()

    assert rendered == [True]
    assert window.current_construction_plan.plan_status == "hold"
    assert window.current_construction_plan.construction_released is False
    assert "仅允许生成非施工放行预览" in window.plan_status_label.text()
    assert "预览不代表施工放行" in window.status_label.text()
    window.close()
    app.processEvents()


def test_construction_review_close_keeps_pending_action_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "construction_plan.json"
    _persist_plan(path)
    pending = ConstructionPlan.model_validate(_plan_payload())

    class RejectedDialog:
        def __init__(self, *_args, **_kwargs):
            self.confirmed_plan = None
            self.current_plan = pending

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr(gui, "ConstructionPlanReviewDialog", RejectedDialog)
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.current_construction_plan = pending

    window.open_construction_plan_review_dialog()

    assert window.current_construction_plan.review_status == "pending_engineer_review"
    assert not window.review_construction_plan_button.isHidden()
    assert window.review_construction_plan_button.isEnabled()
    assert not window.render_checkbox.isEnabled()
    assert "方案保持待确认状态" in window.event_log.toPlainText()
    assert window.start_button.text() == "停止"
    assert not window.start_button.isEnabled()
    window.close()
    app.processEvents()


def test_rendering_guard_allows_confirmed_evidence_inconsistent_preview(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    payload = _plan_payload(plan_status="evidence_inconsistent")
    payload["review_status"] = "confirmed_by_engineer"
    payload["human_review"] = {
        "status": "confirmed_by_engineer",
        "reviewer": "审核工程师",
        "reviewed_at": "2026-09-20T02:00:00+00:00",
        "notes": "已审核但保持阻断",
    }
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    window.current_construction_plan = ConstructionPlan.model_validate(payload)
    window.api_config["repair_render_provider"] = "fhl"
    window._set_primary_button_running(enabled=False)

    assert window._construction_render_allowed() is True
    window.close()
    app.processEvents()


def test_repair_rendering_runs_off_gui_thread_and_reaches_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    assert hasattr(gui, "RepairRenderWorker")
    app = QApplication.instance() or QApplication([])
    entered = threading.Event()
    release = threading.Event()

    class BlockingRenderer:
        def __init__(self, **_kwargs):
            pass

        def render_many(self, items, *, should_stop, on_result):
            entered.set()
            assert release.wait(5)
            result = SimpleNamespace(
                source_path=str(items[0][0]),
                output_path=str(tmp_path / "rendered.jpg"),
                status="success",
                message="完成",
            )
            on_result(result)
            return [result]

    payload = _plan_payload()
    payload["review_status"] = "confirmed_by_engineer"
    payload["human_review"] = {
        "status": "confirmed_by_engineer",
        "reviewer": "线程测试工程师",
        "reviewed_at": "2026-09-20T02:00:00+00:00",
        "notes": "",
    }
    monkeypatch.setattr(gui, "FhlRepairRenderer", BlockingRenderer)
    monkeypatch.setattr(
        gui.DamageRepairPlanner,
        "build_reviewed_render_selection",
        lambda *_args: {
            "items": [(tmp_path / "damage.jpg", "修复工法")],
            "render_count": 1,
            "skipped_no_detection": 0,
            "skipped_failed": 0,
            "skipped_without_plan": 0,
        },
    )
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    window.current_construction_plan = ConstructionPlan.model_validate(payload)
    window.api_config["repair_render_provider"] = "fhl"
    window._set_primary_button_running(enabled=False)

    window.render_repairs()
    assert entered.wait(2)
    worker = window.render_worker
    assert worker is not None and worker.isRunning()
    assert window.start_button.text() == "停止"
    assert window.start_button.isEnabled()
    assert str(tmp_path / "修復渲染") in window.event_log.toPlainText()

    dispatched: list[str] = []
    QTimer.singleShot(0, lambda: dispatched.append("gui-event"))
    app.processEvents()
    assert dispatched == ["gui-event"]

    release.set()
    assert worker.wait(10000)
    app.processEvents()
    assert [result.status for result in window.render_results] == ["success"]
    assert window.start_button.text() == "启动"
    assert window.start_button.isEnabled()
    assert "流程完成" in window.stage_status.text()
    window.close()
    app.processEvents()


def test_repair_rendering_uses_engineer_reviewed_ai_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    payload = _plan_payload()
    payload["review_status"] = "confirmed_by_engineer"
    payload["human_review"] = {
        "status": "confirmed_by_engineer",
        "reviewer": "工法审核工程师",
        "reviewed_at": "2026-09-20T02:00:00+00:00",
        "notes": "",
    }
    payload["work_items"][0].update({
        "ai_method_name": "审核后的低压灌注工法",
        "base_repair_method": "先复测活动性，再清理裂缝并低压灌注，养护后复检",
        "method_rationale": "现场复核后确认适用",
        "repair_method_source": "remote_ai",
    })
    captured: dict[str, object] = {}

    def capture_selection(results, plan_lines):
        captured["results"] = results
        captured["plan_lines"] = plan_lines
        return {
            "items": [],
            "render_count": 0,
            "skipped_no_detection": 0,
            "skipped_failed": 0,
            "skipped_without_plan": 0,
        }

    monkeypatch.setattr(
        gui.DamageRepairPlanner,
        "build_reviewed_render_selection",
        capture_selection,
    )
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": [{"image_name": "crack.jpg"}]}
    window.plan_summary = {"lines": [{"repair_method": "旧规则卡工法"}]}
    window.current_construction_plan = ConstructionPlan.model_validate(payload)

    window.render_repairs()

    plan_lines = captured["plan_lines"]
    assert isinstance(plan_lines, list)
    assert plan_lines == [{
        "image_name": "crack.jpg",
        "method_id": "RC-C02",
        "method_display_name": "审核后的低压灌注工法",
        "repair_method": "先复测活动性，再清理裂缝并低压灌注，养护后复检",
        "original_image_path": "images/crack.jpg",
        "reviewed_method": True,
    }]
    window.close()
    app.processEvents()


def test_primary_stop_cancels_remaining_background_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    assert hasattr(gui, "RepairRenderWorker")
    app = QApplication.instance() or QApplication([])
    entered = threading.Event()
    release = threading.Event()

    class StoppableRenderer:
        def __init__(self, **_kwargs):
            pass

        def render_many(self, items, *, should_stop, on_result):
            results = []
            for index, (path, _method) in enumerate(items):
                if index == 0:
                    entered.set()
                    assert release.wait(5)
                cancelled = should_stop()
                result = SimpleNamespace(
                    source_path=str(path),
                    output_path=None if cancelled else str(tmp_path / f"rendered-{index}.jpg"),
                    status="cancelled" if cancelled else "success",
                    message="已取消" if cancelled else "完成",
                )
                results.append(result)
                on_result(result)
            return results

    payload = _plan_payload()
    payload["review_status"] = "confirmed_by_engineer"
    payload["human_review"] = {
        "status": "confirmed_by_engineer",
        "reviewer": "停止测试工程师",
        "reviewed_at": "2026-09-20T02:00:00+00:00",
        "notes": "",
    }
    monkeypatch.setattr(gui, "FhlRepairRenderer", StoppableRenderer)
    monkeypatch.setattr(
        gui.DamageRepairPlanner,
        "build_reviewed_render_selection",
        lambda *_args: {
            "items": [
                (tmp_path / "damage-1.jpg", "工法一"),
                (tmp_path / "damage-2.jpg", "工法二"),
            ],
            "render_count": 2,
            "skipped_no_detection": 0,
            "skipped_failed": 0,
            "skipped_without_plan": 0,
        },
    )
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    window.current_construction_plan = ConstructionPlan.model_validate(payload)
    window.api_config["repair_render_provider"] = "fhl"

    window.render_repairs()
    assert entered.wait(2)
    worker = window.render_worker
    assert worker is not None
    window.toggle_workflow()
    assert window.start_button.text() == "停止"
    assert not window.start_button.isEnabled()

    release.set()
    assert worker.wait(10000)
    app.processEvents()
    assert all(result.status == "cancelled" for result in window.render_results)
    assert window.start_button.text() == "启动"
    assert window.start_button.isEnabled()
    assert "已停止" in window.stage_status.text()
    window.close()
    app.processEvents()


def test_empty_render_selection_finishes_and_restores_primary_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    payload = _plan_payload()
    payload["review_status"] = "confirmed_by_engineer"
    payload["human_review"] = {
        "status": "confirmed_by_engineer",
        "reviewer": "空选择测试工程师",
        "reviewed_at": "2026-09-20T02:00:00+00:00",
        "notes": "",
    }
    monkeypatch.setattr(
        gui.DamageRepairPlanner,
        "build_reviewed_render_selection",
        lambda *_args: {
            "items": [],
            "render_count": 0,
            "skipped_no_detection": 1,
            "skipped_failed": 0,
            "skipped_without_plan": 0,
        },
    )
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    window.current_construction_plan = ConstructionPlan.model_validate(payload)
    window._set_primary_button_running(enabled=False)

    window.render_repairs()

    assert window.recognition_progress.value() == 100
    assert window.start_button.text() == "启动"
    assert window.start_button.isEnabled()
    assert "跳过" not in window.current_generation_status.text()
    window.close()
    app.processEvents()


def test_render_worker_failure_restores_primary_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui
    from runtime.construction_plan_schema import ConstructionPlan

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    class FailingRenderer:
        def __init__(self, **_kwargs):
            pass

        def render_many(self, _items, *, should_stop, on_result):
            assert not should_stop()
            assert callable(on_result)
            raise RuntimeError("simulated render failure")

    payload = _plan_payload()
    payload["review_status"] = "confirmed_by_engineer"
    payload["human_review"] = {
        "status": "confirmed_by_engineer",
        "reviewer": "失败测试工程师",
        "reviewed_at": "2026-09-20T02:00:00+00:00",
        "notes": "",
    }
    monkeypatch.setattr(gui, "FhlRepairRenderer", FailingRenderer)
    monkeypatch.setattr(
        gui.DamageRepairPlanner,
        "build_reviewed_render_selection",
        lambda *_args: {
            "items": [(tmp_path / "damage.jpg", "修复工法")],
            "render_count": 1,
            "skipped_no_detection": 0,
            "skipped_failed": 0,
            "skipped_without_plan": 0,
        },
    )
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"results": []}
    window.plan_summary = {"lines": []}
    window.current_construction_plan = ConstructionPlan.model_validate(payload)
    window.api_config["repair_render_provider"] = "fhl"

    window.render_repairs()
    worker = window.render_worker
    assert worker is not None
    assert worker.wait(10000)
    app.processEvents()

    assert window.start_button.text() == "启动"
    assert window.start_button.isEnabled()
    assert "修复渲染失败" in window.stage_status.text()
    assert "simulated render failure" in window.event_log.toPlainText()
    window.close()
    app.processEvents()


def test_all_failed_render_results_are_not_reported_as_complete(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window._set_primary_button_running(enabled=True)
    results = [
        SimpleNamespace(
            source_path=str(tmp_path / f"damage-{index}.jpg"),
            output_path=None,
            status="failed",
            message="FHL Images API 未成功回傳（exit 1）：Edit failed: HTTP 502 Bad gateway",
        )
        for index in range(2)
    ]
    for result in results:
        window.on_auto_render_item(result)

    window.on_render_worker_completed({"results": results})

    assert "修复渲染失败" in window.stage_status.text()
    assert "未生成任何修复渲染图" in window.status_label.text()
    assert "2/2" in window.status_label.text()
    assert "HTTP 502" in window.status_label.text()
    log = window.event_log.toPlainText()
    assert "damage-0.jpg" in log and "HTTP 502" in log
    assert "流程完成：全部阶段已结束" not in log
    assert window.start_button.text() == "启动"
    window.close()
    app.processEvents()


def test_partially_failed_render_results_report_exact_counts(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    results = [
        SimpleNamespace(
            source_path=str(tmp_path / "success.jpg"),
            output_path=str(tmp_path / "修復渲染" / "success.png"),
            status="success",
            message="完成",
        ),
        SimpleNamespace(
            source_path=str(tmp_path / "failed.jpg"),
            output_path=None,
            status="failed",
            message="HTTP 502 Bad gateway",
        ),
    ]

    window.on_render_worker_completed({"results": results})

    assert "部分完成" in window.stage_status.text()
    assert "成功 1 张" in window.status_label.text()
    assert "失败 1 张" in window.status_label.text()
    assert "部分完成" in window.current_generation_status.text()
    assert "流程部分完成" in window.event_log.toPlainText()
    window.close()
    app.processEvents()


def test_all_usable_render_results_report_output_count(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    results = [
        SimpleNamespace(
            source_path=str(tmp_path / "success.jpg"),
            output_path=str(tmp_path / "修復渲染" / "success.png"),
            status="success",
            message="完成",
        ),
        SimpleNamespace(
            source_path=str(tmp_path / "resumed.jpg"),
            output_path=str(tmp_path / "修復渲染" / "resumed.png"),
            status="resumed",
            message="沿用",
        ),
    ]

    window.on_render_worker_completed({"results": results})

    assert "流程完成" in window.stage_status.text()
    assert "已生成或沿用 2 张" in window.status_label.text()
    assert "全部阶段已结束" in window.event_log.toPlainText()
    window.close()
    app.processEvents()


def test_render_worker_dispatches_to_siliconflow_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    captured: dict[str, object] = {}

    class SiliconFlowRenderer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def render_many(self, items, *, should_stop, on_result):
            assert not should_stop()
            captured["items"] = items
            captured["on_result"] = on_result
            return []

    class UnexpectedFhlRenderer:
        def __init__(self, **_kwargs):
            raise AssertionError("FHL renderer must not be used")

    monkeypatch.setattr(gui, "SiliconFlowRepairRenderer", SiliconFlowRenderer)
    monkeypatch.setattr(gui, "FhlRepairRenderer", UnexpectedFhlRenderer)
    worker = gui.RepairRenderWorker(
        [(tmp_path / "damage.jpg", "修复工法")],
        tmp_path / "renders",
        {
            "repair_render_provider": "siliconflow",
            "siliconflow_url": "https://api.siliconflow.cn/v1",
            "siliconflow_key": "test-key",
            "siliconflow_model": "Qwen/Qwen-Image-Edit-2509",
        },
        "保守修复",
    )
    completed: list[object] = []
    failures: list[str] = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failures.append)

    worker.run()
    app.processEvents()

    assert failures == []
    assert completed == [{"results": []}]
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://api.siliconflow.cn/v1"
    assert captured["model"] == "Qwen/Qwen-Image-Edit-2509"
    assert captured["base_prompt"] == "保守修复"


def test_render_worker_keeps_fhl_credentials_separate_from_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    captured: dict[str, object] = {}

    class FhlRenderer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def render_many(self, items, *, should_stop, on_result):
            captured["items"] = items
            return []

    class UnexpectedSiliconFlowRenderer:
        def __init__(self, **_kwargs):
            raise AssertionError("SiliconFlow renderer must not be used")

    monkeypatch.setattr(gui, "FhlRepairRenderer", FhlRenderer)
    monkeypatch.setattr(gui, "SiliconFlowRepairRenderer", UnexpectedSiliconFlowRenderer)
    worker = gui.RepairRenderWorker(
        [(tmp_path / "damage.jpg", "修复工法")],
        tmp_path / "renders",
        {
            "repair_render_provider": "fhl",
            "responses_url": "https://responses.example.test/v1",
            "responses_key": "responses-secret",
            "fhl_url": "https://images.example.test/v1/images/edits",
            "fhl_key": "fhl-secret",
            "siliconflow_url": "https://silicon.example.test/v1",
            "siliconflow_key": "silicon-secret",
        },
        "保守修复",
    )
    completed: list[object] = []
    failures: list[str] = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failures.append)

    worker.run()
    app.processEvents()

    assert failures == []
    assert completed == [{"results": []}]
    assert captured["api_key"] == "fhl-secret"
    assert captured["api_url"] == "https://images.example.test/v1/images/edits"
    assert "responses-secret" not in captured.values()
    assert "silicon-secret" not in captured.values()

    captured.clear()
    worker = gui.RepairRenderWorker(
        [(tmp_path / "damage.jpg", "修复工法")],
        tmp_path / "renders",
        {
            "repair_render_provider": "fhl",
            "responses_key": "responses-secret",
            "fhl_url": "https://images.example.test/v1/images/edits",
            "fhl_key": "",
            "siliconflow_key": "silicon-secret",
        },
        "保守修复",
    )
    worker.run()
    app.processEvents()
    assert captured["api_key"] is None
    assert "responses-secret" not in captured.values()
    assert "silicon-secret" not in captured.values()
