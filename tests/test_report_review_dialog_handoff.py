from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


def _report_payload(*, status: str = "pending_human_review") -> dict:
    reviewed_at = "2026-09-20T01:00:00+00:00" if status == "confirmed_by_human" else None
    reviewer = "张工" if status == "confirmed_by_human" else ""
    return {
        "subject": {
            "project_name": "测试项目",
            "asset_id": "A-1",
            "component": "框架柱",
            "inspection_time": None,
            "project_overview": "500×500框架柱",
        },
        "executive_summary": "发现一处结构裂缝。",
        "overall_screening_level": "medium",
        "overall_level_reason": "视觉证据显示连续裂缝。",
        "findings": [{
            "finding_index": 0,
            "image_name": "crack.jpg",
            "damage_type": "Structural crack",
            "damage_level": "medium",
            "level_reason": "原图可见连续裂缝。",
            "standards_basis": [{
                "id": "GB 50292-2015",
                "name": "民用建筑可靠性鉴定标准",
                "role": "裂缝等级复核依据 [KB:doc-standard:page:12]",
            }],
            "visual_basis": ["原图与识别覆盖图位置一致"],
            "uncertainty": "无物理尺度。",
            "observed_evidence": "视觉上可见裂缝。",
            "risk_interpretation": "需现场复核。",
            "recommended_action": "现场复测后确定工法。",
            "confidence_note": "识别置信度不等于结构安全结论。",
        }],
        "limitations": ["无物理尺度。"],
        "report_schema_version": "damage-report.v3",
        "review_status": status,
        "human_review": {
            "status": status,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "notes": "",
        },
        "integrity": {
            "correspondence_valid": True,
            "expected_count": 1,
            "actual_count": 1,
            "issues": [],
        },
        "provenance": {
            "model": "vision-model",
            "generated_at": "2026-09-20T00:00:00+00:00",
            "source_summary_path": "batch_summary.json",
            "evidence_count": 1,
            "report_schema_version": "damage-report.v3",
            "human_review_required": True,
        },
    }


def _report(*, status: str = "pending_human_review"):
    from runtime.damage_report_schema import DamageReport

    return DamageReport.model_validate(_report_payload(status=status))


def _persist_report(path: Path, *, status: str = "pending_human_review") -> None:
    path.write_text(
        json.dumps(
            {
                "report": _report_payload(status=status),
                "evidence_snapshot": {
                    "detection_hints": [{"image_name": "crack.jpg", "finding_index": 0}]
                },
                "report_schema_version": "damage-report.v3",
                "fallback": False,
                "fallback_reason": "",
                "generation_audit": {
                    "generation_mode": "remote_vision_ai",
                    "model": "vision-model",
                    "knowledge_base_used": False,
                    "knowledge_base_status": "not_used",
                    "fallback_reason": "",
                    "visual_attachments": [],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_generation_context_normalizes_pydantic_report_to_json() -> None:
    from runtime.generation_context import build_generation_context
    from runtime.settings_models import default_profiles

    report = _report(status="confirmed_by_human")
    context = build_generation_context(
        default_profiles()["施工方案"],
        {"validated_report": report},
        settings_snapshot_id="snapshot",
    )

    assert isinstance(context.evidence["validated_report"], dict)
    assert context.evidence["validated_report"]["review_status"] == "confirmed_by_human"
    json.dumps(context.evidence, ensure_ascii=False)


def test_generation_context_normalizes_supported_nested_evidence(tmp_path: Path) -> None:
    from runtime.generation_context import build_generation_context
    from runtime.settings_models import default_profiles

    @dataclass
    class EvidenceItem:
        source: Path
        inspected_on: date

    context = build_generation_context(
        default_profiles()["施工方案"],
        {
            "item": EvidenceItem(tmp_path / "source.pdf", date(2026, 9, 20)),
            "generated_at": datetime(2026, 9, 20, 1, 2, tzinfo=timezone.utc),
            "values": (1, True, None),
        },
        settings_snapshot_id="snapshot",
    )

    assert context.evidence["item"]["source"] == str(tmp_path / "source.pdf")
    assert context.evidence["item"]["inspected_on"] == "2026-09-20"
    assert context.evidence["generated_at"] == "2026-09-20T01:02:00+00:00"
    assert context.evidence["values"] == [1, True, None]


def test_generation_context_rejects_unsupported_evidence_object() -> None:
    from runtime.generation_context import build_generation_context
    from runtime.settings_models import default_profiles

    with pytest.raises(TypeError, match=r"Evidence serialization error at evidence\.unsupported"):
        build_generation_context(
            default_profiles()["施工方案"],
            {"unsupported": object()},
            settings_snapshot_id="snapshot",
        )


def test_construction_evidence_contains_json_mapping_not_damage_report() -> None:
    import runtime.damage_workflow_gui as gui

    evidence = gui._construction_evidence(
        {"project_overview": "测试", "results": []},
        _report(status="confirmed_by_human"),
        {"repair_plan_version": "repair-method.v2", "lines": []},
    )

    assert isinstance(evidence["validated_report"], dict)
    assert evidence["validated_report"]["human_review"]["reviewer"] == "张工"
    json.dumps(evidence, ensure_ascii=False)


def test_review_dialog_is_structured_readable_and_can_confirm(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication([])
    report_path = tmp_path / "report.json"
    _persist_report(report_path)
    dialog = gui.DamageReportReviewDialog(_report(), report_path)

    assert dialog.isModal()
    assert dialog.windowTitle() == "损伤分析报告人工审核"
    assert dialog.tabs.currentIndex() == 0
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "总体信息",
        "损伤明细（1）",
        "复核与局限",
        "原始数据",
    ]
    assert dialog.subject_edits["inspection_time"].text() == ""
    assert "null" not in dialog.subject_edits["inspection_time"].text().casefold()
    assert dialog.overall_level_combo.currentText() == "中"
    assert dialog.overall_level_combo.currentData() == "medium"
    assert dialog.raw_report_editor.isReadOnly() is True
    standards_text = dialog.finding_editors[0]["standards_label"].text()
    assert "民用建筑可靠性鉴定标准" in standards_text
    assert "第12页" in standards_text
    assert "[KB:" not in standards_text
    assert dialog.save_draft_button.text() == "保存审核草稿"
    assert dialog.confirm_button.text() == "确认报告并生成施工方案"
    dialog.reviewer_edit.setText("李工")
    dialog.confirm_review()

    assert dialog.result() == QDialog.Accepted
    assert dialog.confirmed_report is not None
    assert dialog.confirmed_report.human_review.reviewer == "李工"
    persisted = json.loads(report_path.read_text(encoding="utf-8"))["report"]
    assert persisted["review_status"] == "confirmed_by_human"
    assert persisted["findings"][0]["standards_basis"][0]["role"].endswith(
        "[KB:doc-standard:page:12]"
    )
    dialog.deleteLater()
    app.processEvents()


def test_review_dialog_saves_pending_draft_without_closing(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    report_path = tmp_path / "report.json"
    _persist_report(report_path)
    dialog = gui.DamageReportReviewDialog(_report(), report_path)
    dialog.executive_summary_edit.setPlainText("人工修改后的摘要。")
    dialog.overall_level_combo.setCurrentIndex(
        dialog.overall_level_combo.findData("high")
    )
    dialog.finding_editors[0]["damage_level"].setCurrentIndex(
        dialog.finding_editors[0]["damage_level"].findData("low")
    )
    dialog.reviewer_edit.setText("王工")
    dialog.review_notes_edit.setPlainText("已现场核对照片。")
    dialog.limitations_edit.setPlainText("无物理尺度。\n\n缺少材料试验。")

    dialog.save_draft()

    persisted = json.loads(report_path.read_text(encoding="utf-8"))["report"]
    assert persisted["executive_summary"] == "人工修改后的摘要。"
    assert persisted["overall_screening_level"] == "high"
    assert persisted["findings"][0]["damage_level"] == "low"
    assert persisted["limitations"] == ["无物理尺度。", "缺少材料试验。"]
    assert persisted["review_status"] == "edited_pending_confirmation"
    assert persisted["human_review"]["reviewer"] == "王工"
    assert persisted["human_review"]["notes"] == "已现场核对照片。"
    assert dialog.result() == 0
    assert dialog.confirmed_report is None
    dialog.deleteLater()
    app.processEvents()


def test_review_dialog_rebuilds_only_editable_fields_and_syncs_readonly_raw_data(
    tmp_path: Path,
) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    report_path = tmp_path / "report.json"
    original = _report()
    _persist_report(report_path)
    dialog = gui.DamageReportReviewDialog(original, report_path)
    dialog.subject_edits["project_name"].setText("人工复核项目")
    dialog.executive_summary_edit.setPlainText("结构化表单修改后的摘要。")
    dialog.finding_editors[0]["damage_type"].setText("裂缝")

    rebuilt = dialog._report_from_form()

    assert rebuilt.subject.project_name == "人工复核项目"
    assert rebuilt.findings[0].damage_type == "裂缝"
    assert rebuilt.findings[0].image_name == original.findings[0].image_name
    assert rebuilt.findings[0].finding_index == original.findings[0].finding_index
    assert rebuilt.findings[0].standards_basis == original.findings[0].standards_basis
    assert rebuilt.report_schema_version == original.report_schema_version
    assert rebuilt.provenance == original.provenance
    assert rebuilt.integrity == original.integrity

    dialog.tabs.setCurrentIndex(dialog.raw_tab_index)
    app.processEvents()
    raw_payload = json.loads(dialog.raw_report_editor.toPlainText())
    assert raw_payload["executive_summary"] == "结构化表单修改后的摘要。"
    assert raw_payload["subject"]["inspection_time"] is None
    assert dialog.raw_report_editor.isReadOnly()
    assert not hasattr(dialog, "report_editor")
    dialog.deleteLater()
    app.processEvents()


def test_report_completion_schedules_review_dialog(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    calls: list[bool] = []
    window.open_report_review_dialog = lambda: calls.append(True)

    window.on_auto_report_done(_report())
    app.processEvents()

    assert calls == [True]
    assert window.review_report_button.isEnabled()
    assert not window.review_report_button.isHidden()
    window.close()


def test_report_review_wait_keeps_primary_action_in_running_state() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()

    window.on_auto_completed({"awaiting_human_review": True})

    assert window.start_button.text() == "停止"
    assert window.start_button.objectName() == "stopButton"
    assert not window.start_button.isEnabled()
    window.close()
    app.processEvents()


def test_review_dialog_cancel_leaves_report_pending(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    report_path = tmp_path / "report.json"
    _persist_report(report_path)
    dialog = gui.DamageReportReviewDialog(_report(), report_path)
    dialog.reject()

    persisted = json.loads(report_path.read_text(encoding="utf-8"))["report"]
    assert persisted["review_status"] == "pending_human_review"
    assert dialog.confirmed_report is None
    dialog.deleteLater()
    app.processEvents()


def test_confirmed_report_immediately_keeps_primary_button_in_stop_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication([])
    report_path = tmp_path / "report.json"
    _persist_report(report_path)
    confirmed = _report(status="confirmed_by_human")

    class AcceptedDialog:
        def __init__(self, *_args, **_kwargs):
            self.confirmed_report = confirmed
            self.current_report = confirmed

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(gui, "DamageReportReviewDialog", AcceptedDialog)
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.current_report = _report()
    window._plan_generation_scheduled = True
    window._render_profile_docx = lambda *_args, **_kwargs: None

    window.open_report_review_dialog()

    assert window.start_button.text() == "停止"
    assert window.start_button.objectName() == "stopButton"
    assert not window.start_button.isEnabled()
    window.close()
    app.processEvents()


def test_confirmed_report_builds_local_construction_plan_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QMessageBox
    from runtime.knowledge_base import KnowledgeBaseSearchResult

    app = QApplication.instance() or QApplication([])
    _persist_report(tmp_path / "report.json", status="confirmed_by_human")
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {
        "project_overview": "500×500框架柱",
        "results": [{
            "image_name": "crack.jpg",
            "status": "success",
            "damage_findings": [{
                "index": 0,
                "class_name": "Structural crack",
                "score": 0.91,
            }],
        }],
    }
    window.current_report = _report(status="confirmed_by_human")
    window.api_config["responses_key"] = ""
    window.render_checkbox.setChecked(False)
    review_scheduled: list[bool] = []
    window._schedule_construction_plan_review_dialog = lambda: review_scheduled.append(True)
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gui,
        "_retrieve_profile_knowledge",
        lambda *_args, **_kwargs: KnowledgeBaseSearchResult((), scope_available=False),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    window.build_plan()
    worker = window.plan_worker
    assert worker is not None
    assert worker.isRunning()
    assert window.start_button.text() == "停止"
    assert window.start_button.objectName() == "stopButton"
    duplicate = window.plan_worker
    window.build_plan()
    assert window.plan_worker is duplicate
    assert window.current_generation_status.text().startswith("生成中")
    assert window.recognition_progress.value() >= 55
    assert not window.start_button.isEnabled()
    assert worker.wait(10000)
    app.processEvents()

    assert warnings == []
    assert (tmp_path / "repair_plan.json").is_file()
    assert (tmp_path / "construction_plan.json").is_file()
    assert (tmp_path / "construction_plan.md").is_file()
    persisted = json.loads((tmp_path / "construction_plan.json").read_text(encoding="utf-8"))
    assert persisted["construction_plan"]["plan_status"] == "pending_engineer_review"
    assert review_scheduled == [True]
    assert not window.review_construction_plan_button.isHidden()
    assert not window.render_checkbox.isEnabled()
    assert window.current_generation_status.text() == "已完成（本地降级）"
    assert window.review_report_button.isHidden()
    assert window.plan_worker is None
    assert window.start_button.text() == "停止"
    assert window.start_button.objectName() == "stopButton"
    assert not window.start_button.isEnabled()
    window.close()
    app.processEvents()


def test_construction_failure_keeps_confirmed_report_available_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.damage_workflow_gui as gui
    import runtime.responses_damage_report as report_runtime

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"project_overview": "测试", "results": []}
    confirmed_report = _report(status="confirmed_by_human")
    window.current_report = confirmed_report
    warnings: list[str] = []
    monkeypatch.setattr(
        report_runtime,
        "load_confirmed_report",
        lambda _path: (_ for _ in ()).throw(RuntimeError("simulated plan failure")),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    window.build_plan()
    worker = window.plan_worker
    assert worker is not None
    assert worker.wait(10000)
    app.processEvents()

    assert window.current_report is confirmed_report
    assert window.review_report_button.isEnabled()
    assert window.review_report_button.text() == "重新生成施工方案"
    assert not window.review_report_button.isHidden()
    assert "可重試" in window.current_generation_status.text()
    assert warnings == ["RuntimeError: simulated plan failure"]
    assert window.plan_worker is None
    window.close()
    app.processEvents()


def test_construction_worker_wait_does_not_block_qt_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.damage_workflow_gui as gui
    import runtime.responses_damage_report as report_runtime

    if gui.GUI_IMPORT_ERROR is not None:
        pytest.skip(str(gui.GUI_IMPORT_ERROR))
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.summary = {"project_overview": "线程响应测试", "results": []}
    window.current_report = _report(status="confirmed_by_human")
    entered = threading.Event()
    release = threading.Event()
    warnings: list[str] = []

    def delayed_failure(_path):
        entered.set()
        assert release.wait(5)
        raise RuntimeError("delayed worker failure")

    monkeypatch.setattr(report_runtime, "load_confirmed_report", delayed_failure)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    window.build_plan()
    worker = window.plan_worker
    assert worker is not None
    assert entered.wait(2)

    dispatched: list[str] = []
    QTimer.singleShot(0, lambda: dispatched.append("gui-event"))
    app.processEvents()
    assert dispatched == ["gui-event"]
    assert worker.isRunning()

    release.set()
    assert worker.wait(10000)
    app.processEvents()
    assert warnings == ["RuntimeError: delayed worker failure"]
    assert window.plan_worker is None
    window.close()
    app.processEvents()
