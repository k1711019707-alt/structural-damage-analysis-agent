from __future__ import annotations

import os
import json
from pathlib import Path


def _save_grab(
    widget: object,
    path: Path,
    *,
    minimum_width: int = 980,
    minimum_height: int = 680,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    assert not pixmap.isNull()
    assert pixmap.width() >= minimum_width
    assert pixmap.height() >= minimum_height
    assert pixmap.save(str(path), "PNG")
    assert path.stat().st_size > 10_000


def _review_report():
    from runtime.damage_report_schema import DamageReport

    return DamageReport.model_validate({
        "subject": {
            "project_name": "视觉测试项目",
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
            "standards_basis": [],
            "visual_basis": ["原图与识别覆盖图位置一致"],
            "uncertainty": "无物理尺度。",
            "observed_evidence": "视觉上可见裂缝。",
            "risk_interpretation": "需现场复核。",
            "recommended_action": "现场复测后确定工法。",
            "confidence_note": "识别置信度不等于结构安全结论。",
        }],
        "limitations": ["无物理尺度。"],
        "report_schema_version": "damage-report.v3",
        "review_status": "pending_human_review",
        "human_review": {
            "status": "pending_human_review",
            "reviewer": "",
            "reviewed_at": None,
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
    })


def test_main_and_settings_visual_shells(tmp_path: Path) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QListWidget,
        QLineEdit,
        QPlainTextEdit,
        QStackedWidget,
        QTreeWidget,
    )

    import runtime.damage_workflow_gui as gui

    app = QApplication.instance() or QApplication([])
    output_dir = Path(os.environ.get("GUI_VISUAL_ARTIFACT_DIR", tmp_path))

    window = gui.DamageWorkflowWindow()
    window.resize(1680, 980)
    window.show()
    app.processEvents()

    assert window.width() == 1680
    assert window.height() == 980
    assert window.preview.isVisible() and window.preview.width() >= 480
    assert window.image_list.isVisible() and window.image_list.width() >= 180
    assert window.findings_table.isVisible() and window.findings_table.width() >= 400
    column_widths = [window.findings_table.columnWidth(index) for index in range(6)]
    assert all(width > 0 for width in column_widths)
    assert column_widths[2] == max(column_widths)
    total_column_width = sum(column_widths)
    assert abs(total_column_width - window.findings_table.viewport().width()) <= 2
    for actual, expected in zip(column_widths, gui.DETECTION_RESULT_COLUMN_PROPORTIONS):
        assert abs(actual / total_column_width - expected) <= 0.01
    assert window.start_button.isVisible() and window.start_button.text() == "启动"
    assert window.render_checkbox.isVisible()
    assert window.start_button.height() == window.render_checkbox.height()
    assert window.project_overview_edit.verticalScrollBarPolicy().name == "ScrollBarAsNeeded"
    assert window.project_overview_title.objectName() == "projectOverviewTitle"
    assert window.project_overview_edit.height() > window.project_overview_title.height()
    assert window.project_overview_edit.parentWidget().geometry().bottom() < window._responsive_widgets["document_panel"].geometry().top()
    assert window.actions_panel.y() > window.preview.y()
    assert window.workflow_progress_bar.isVisible()
    assert window.open_report_word_button.geometry().bottom() < window.start_button.geometry().top()
    assert window.open_construction_plan_button.geometry().bottom() < window.render_checkbox.geometry().top()
    assert window.event_log_title.geometry().top() == window.current_file_title.geometry().top()
    assert window.event_log.geometry().top() > window.event_log_title.geometry().bottom()
    assert window.event_log.height() > 100
    assert window.event_log.geometry().bottom() <= window._responsive_widgets["log_panel"].contentsRect().bottom()
    assert not any(button.isVisible() for button in getattr(window, "stage_buttons", []))
    _save_grab(window, output_dir / "actual-main-workbench-1680x980.png")
    window.resize(1440, 980)
    app.processEvents()
    assert window.width() == 1440
    assert window.preview.isVisible() and window.findings_table.isVisible()
    assert window.start_button.isVisible() and window.render_checkbox.isVisible()
    assert window.workflow_progress_bar.isVisible()
    assert window.open_report_word_button.geometry().bottom() < window.start_button.geometry().top()
    assert window.open_construction_plan_button.geometry().bottom() < window.render_checkbox.geometry().top()
    assert window.review_report_button.isHidden()
    assert not hasattr(window, "read_document_button")
    assert not hasattr(window, "report_review_panel")
    _save_grab(window, output_dir / "actual-main-workbench-1440x980.png")

    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "report": _review_report().model_dump(mode="json"),
                "evidence_snapshot": {
                    "detection_hints": [{"image_name": "crack.jpg", "finding_index": 0}]
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    review_dialog = gui.DamageReportReviewDialog(_review_report(), report_path, window)
    review_dialog.resize(1100, 760)
    review_dialog.show()
    app.processEvents()
    assert review_dialog.isModal()
    assert review_dialog.tabs.isVisible()
    assert review_dialog.tabs.currentIndex() == 0
    assert review_dialog.overall_tab.isVisible()
    assert not review_dialog.raw_report_editor.isVisible()
    assert review_dialog.tabs.geometry().bottom() < review_dialog.status_label.geometry().top()
    assert review_dialog.confirm_button.geometry().bottom() <= review_dialog.height()
    _save_grab(review_dialog, output_dir / "actual-report-review-dialog-1100x760.png")
    review_dialog.tabs.setCurrentIndex(1)
    app.processEvents()
    assert review_dialog.findings_tab.isVisible()
    assert review_dialog.finding_editors[0]["group"].isVisible()
    assert review_dialog.finding_editors[0]["standards_label"].isVisible()
    _save_grab(review_dialog, output_dir / "actual-report-review-findings-1100x760.png")
    review_dialog.resize(900, 650)
    review_dialog.tabs.setCurrentIndex(2)
    app.processEvents()
    assert review_dialog.tabs.isVisible()
    assert review_dialog.review_tab.isVisible()
    assert review_dialog.reviewer_edit.isVisible()
    assert review_dialog.limitations_edit.isVisible()
    assert review_dialog.confirm_button.geometry().right() <= review_dialog.width()
    _save_grab(
        review_dialog,
        output_dir / "actual-report-review-reviewer-900x650.png",
        minimum_width=900,
        minimum_height=650,
    )
    review_dialog.close()
    window.resize(1680, 980)
    app.processEvents()

    captured: dict[str, object] = {}

    def inspect_settings_dialog() -> None:
        dialogs = [
            widget
            for widget in app.topLevelWidgets()
            if isinstance(widget, QDialog) and widget.isVisible()
        ]
        if not dialogs:
            captured["error"] = "settings dialog did not become visible"
            return
        dialog = dialogs[0]
        dialog.resize(980, 680)
        app.processEvents()
        button_box = dialog.findChild(QDialogButtonBox)
        captured["minimum_layout_visible"] = bool(
            button_box is not None
            and button_box.isVisible()
            and button_box.geometry().bottom() <= dialog.height()
        )
        dialog.resize(1180, 800)
        app.processEvents()
        nav = dialog.findChild(QListWidget, "settingsNav")
        stack = dialog.findChild(QStackedWidget)
        captured.update(dialog=dialog, nav=nav, stack=stack)
        if nav is not None:
            nav.setCurrentRow(1)
            app.processEvents()
        knowledge_page = stack.currentWidget() if stack is not None else None
        trees = knowledge_page.findChildren(QTreeWidget) if knowledge_page is not None else []
        captured["tree_visible"] = bool(len(trees) >= 2 and all(tree.isVisible() for tree in trees[:2]))
        profile_controls: list[tuple[int, int, int]] = []
        if nav is not None and stack is not None:
            for row in (2, 3):
                nav.setCurrentRow(row)
                app.processEvents()
                page = stack.currentWidget()
                profile_controls.append((
                    len(page.findChildren(QPlainTextEdit)),
                    len(page.findChildren(QTreeWidget)),
                    len(page.findChildren(QLineEdit)),
                ))
            nav.setCurrentRow(1)
            app.processEvents()
        captured["profile_controls"] = profile_controls
        _save_grab(dialog, output_dir / "actual-settings-knowledge-base-1180x800.png")
        dialog.reject()

    QTimer.singleShot(50, inspect_settings_dialog)
    window.open_settings()

    assert "error" not in captured
    nav = captured["nav"]
    stack = captured["stack"]
    assert isinstance(nav, QListWidget)
    assert isinstance(stack, QStackedWidget)
    assert nav.count() == 5
    assert captured["minimum_layout_visible"] is True
    assert [nav.item(index).text() for index in range(nav.count())] == [
        "API",
        "知识库",
        "损伤分析报告",
        "施工方案",
        "修复渲染图",
    ]
    assert 210 <= nav.width() <= 230
    assert stack.currentIndex() == 1
    knowledge_page = stack.currentWidget()
    assert captured["tree_visible"] is True
    assert captured["profile_controls"] == [(1, 1, 1), (1, 1, 1)]
    assert stack.x() > nav.x() + nav.width()

    captured.clear()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_project_overview_band_matches_approved_layout() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()

    assert window.project_overview_edit.parentWidget().objectName() == "projectOverviewPanel"
    assert window.project_overview_edit.width() >= window.findings_table.width()
    assert window.project_overview_counter.text() == "0 / 4000"
    assert window.project_overview_title.objectName() == "projectOverviewTitle"
    assert window.project_overview_edit.sizePolicy().verticalPolicy().name == "Expanding"
    assert window.project_overview_edit.minimumHeight() >= 42
    assert window.project_overview_edit.maximumHeight() >= 70
    window.close()
