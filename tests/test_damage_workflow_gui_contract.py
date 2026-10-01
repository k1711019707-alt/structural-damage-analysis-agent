from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np


def test_workflow_uses_traditional_chinese_four_stage_order() -> None:
    import runtime.damage_workflow_gui as gui

    assert gui.STAGES == ("損傷識別", "損傷分析報告", "修復施工方案", "修復後渲染圖")
    assert "→" in gui.STAGE_HELP[0] or "YOLO" in gui.STAGE_HELP[0]


def test_workflow_module_exposes_new_default_entrypoint() -> None:
    import scripts.launch_yolo11s_seg_gui as launcher
    import runtime.damage_workflow_gui as gui

    assert callable(launcher.main)
    assert hasattr(gui, "main")


def test_recognition_worker_uses_shared_full_inspection_inference() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    text = source.read_text(encoding="utf-8")

    assert "inference = runtime.infer_inspection(image)" in text
    assert "inference = runtime.infer(image)" not in text


def test_gui_adds_one_result_row_for_each_inspection_finding() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.on_image_done(
        "multi.jpg",
        {
            "image_name": "multi.jpg",
            "status": "success",
            "damage_findings": [
                {"index": 0, "class_name": "Structural crack", "score": 0.91, "screening_severity": {"level": "high"}},
                {"index": 1, "class_name": "Rebar corrosion", "score": 0.83, "screening_severity": {"level": "medium"}},
            ],
        },
    )

    assert window.findings_table.rowCount() == 2
    assert [window.findings_table.item(row, 1).text() for row in range(2)] == [
        "multi.jpg",
        "multi.jpg",
    ]
    assert [window.findings_table.item(row, 2).text() for row in range(2)] == [
        "Structural crack",
        "Rebar corrosion",
    ]
    assert [window.findings_table.item(row, 0).text() for row in range(2)] == ["1", "2"]
    assert [window.findings_table.item(row, 4).text() for row in range(2)] == ["严重", "中等"]
    window.close()


def test_detection_rows_use_table_wide_numbers_and_conservative_severity_labels() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    first = {
        "index": 0,
        "class_name": "Structural crack",
        "score": 0.9,
        "screening_severity": {"level": "LOW"},
    }
    window.on_image_done("a.jpg", {"status": "success", "damage_findings": [first]})
    window.on_image_done(
        "b.jpg",
        {
            "status": "success",
            "damage_findings": [
                {"index": 0, "class_name": "Spalling", "score": 0.8, "screening_severity": {"level": "medium"}},
                {"index": 1, "class_name": "Corrosion", "score": 0.7, "screening_severity": {"level": "future-level"}},
                {"index": 2, "class_name": "Crack", "score": 0.6},
            ],
        },
    )
    window.on_image_done("clean.jpg", {"status": "no_detection", "damage_findings": []})
    window.on_image_done("failed.jpg", {"status": "failed", "error": "read error", "damage_findings": []})

    assert [window.findings_table.item(row, 0).text() for row in range(6)] == [
        "1", "2", "3", "4", "5", "6",
    ]
    assert [window.findings_table.item(row, 1).text() for row in range(6)] == [
        "a.jpg", "b.jpg", "b.jpg", "b.jpg", "clean.jpg", "failed.jpg",
    ]
    assert [window.findings_table.item(row, 4).text() for row in range(6)] == [
        "轻微", "中等", "待判定", "待判定", "—", "—",
    ]
    assert first["index"] == 0
    assert window.findings_table.item(0, 1).toolTip() == "a.jpg"
    assert "不是结构安全等级" in window.findings_table.item(0, 4).toolTip()
    window.close()


def test_detection_csv_exports_display_number_and_screening_severity(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window.on_image_done(
        "damage.jpg",
        {
            "status": "success",
            "damage_findings": [{
                "index": 0,
                "class_name": "Structural deformation",
                "score": 0.95,
                "screening_severity": {"level": "high"},
            }],
        },
    )

    window.export_findings()

    rows = (tmp_path / "findings.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0] == "编号,文件名,损伤类型,置信度,损伤等级,状态"
    assert rows[1].split(",") == ["1", "damage.jpg", "Structural deformation", "95.0%", "严重", "已完成"]
    window.close()


def test_automatic_worker_configures_retries_and_local_report_fallback() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    text = source.read_text(encoding="utf-8")
    assert "retries=2" in text
    assert "build_local_fallback_report_from_summary" in text
    assert "远程报告服务不可用，已生成本地证据报告并继续流程" in text


def test_missing_knowledge_scope_does_not_block_remote_ai_generation() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    text = source.read_text(encoding="utf-8")

    assert "if report_kb_error:\n                        raise ValueError(report_kb_error)" not in text
    assert "if plan_kb_error:\n                        raise ValueError(plan_kb_error)" not in text
    assert "知识库不可用，将仅依据结构化证据继续调用 AI" in text


def test_profile_folder_scope_is_not_intersected_with_global_document_selection(monkeypatch) -> None:
    import runtime.damage_workflow_gui as gui

    captured = {}

    def fake_pipeline_search(_adapter, _query, **kwargs):
        captured.update(kwargs)
        return gui.KnowledgeBaseSearchResult((), scope_available=False)

    monkeypatch.setattr(gui, "pipeline_search_with_scope", fake_pipeline_search)
    gui._retrieve_profile_knowledge(
        Path("unused"),
        SimpleNamespace(
            knowledge_base_document_ids=[],
            knowledge_base_folder_ids=["report-folder"],
        ),
        SimpleNamespace(
            enabled_document_ids=["construction-document"],
            top_k=6,
            max_context_chars=10000,
        ),
        {"results": []},
    )

    assert captured["document_ids"] == ()
    assert captured["folder_ids"] == ("report-folder",)


def test_render_and_repair_plan_services_are_separate_from_gui() -> None:
    from runtime.damage_repair_plan import DamageRepairPlanner
    from runtime.fhl_repair_renderer import FhlRepairRenderer

    assert callable(DamageRepairPlanner().build_summary)
    assert callable(FhlRepairRenderer.render_one)


def test_gui_uses_unicode_safe_image_io(tmp_path: Path) -> None:
    import runtime.damage_workflow_gui as gui

    source = tmp_path / "損傷圖片"
    source.mkdir()
    encoded_ok, encoded = cv2.imencode(".jpg", np.zeros((8, 8, 3), dtype=np.uint8))
    assert encoded_ok
    image_path = source / "測試.jpg"
    image_path.write_bytes(encoded.tobytes())

    image = gui._read_image_unicode(image_path, cv2)
    assert image is not None
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    pixmap = gui._load_pixmap_unicode(image_path)
    assert not pixmap.isNull()

    output_path = source / "輸出覆蓋.jpg"
    gui._write_image_unicode(output_path, image, cv2)
    assert output_path.is_file()


def test_report_auth_error_is_actionable_without_leaking_key() -> None:
    import runtime.damage_workflow_gui as gui

    secret = "uuid-secret-value-123456"
    message = gui._format_report_api_error(
        RuntimeError("AuthenticationError: 401 INVALID_API_KEY"),
        base_url="https://www.fhl.mom/v1",
        model="gpt-5.6-sol",
        api_key=secret,
    )
    assert "認證失敗" in message
    assert "FHL Image Gen" in message
    assert secret not in message


def test_report_transient_server_error_is_actionable() -> None:
    import runtime.damage_workflow_gui as gui

    message = gui._format_report_api_error(
        RuntimeError("Responses API report generation failed: InternalServerError (HTTP 500, 3 attempts)"),
        base_url="https://www.fhl.mom/v1",
        model="gpt-5.6-sol",
        api_key="secret-key",
    )
    assert "服务暂时不可用" in message
    assert "稍后重试" in message
    assert "secret-key" not in message


def test_report_protocol_error_is_actionable() -> None:
    import runtime.damage_workflow_gui as gui

    message = gui._format_report_api_error(
        RuntimeError("BadRequestError: protocol_not_supported: model does not support chat completions"),
        base_url="https://cf.api.fan/v1",
        model="gpt-5.6-sol",
        api_key="secret-key",
    )

    assert "不支持当前调用协议" in message
    assert "Chat Completions" in message
    assert "secret-key" not in message


def test_dynamic_hud_initial_controls_and_render_visibility() -> None:
    from PySide6.QtWidgets import QApplication, QHeaderView
    import runtime.damage_workflow_gui as gui

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    assert window.start_button.text() == "启动"
    assert window.cancel_button is window.start_button
    assert window.render_checkbox.text() == "生成修复渲染图"
    assert window.render_checkbox.toolTip() == "生成修复渲染图"
    assert window.findings_table.columnCount() == 6
    assert [window.findings_table.horizontalHeaderItem(index).text() for index in range(6)] == ["编号", "文件名", "损伤类型", "置信度", "损伤等级", "状态"]
    assert "不是结构安全等级" in window.findings_table.horizontalHeaderItem(4).toolTip()
    header = window.findings_table.horizontalHeader()
    assert all(header.sectionResizeMode(index) == QHeaderView.Fixed for index in range(6))
    assert window.findings_table.column_proportions == gui.DETECTION_RESULT_COLUMN_PROPORTIONS
    assert window.findings_table.verticalHeader().isVisible() is False
    assert not window.render_stop_button.isVisible()
    assert "GPU 加速" in window.gpu_status.text()
    assert window.event_log is not None
    assert window.current_file.text() == "—"
    window.on_auto_stage("修復後渲染圖生成", "渲染中", 75)
    assert window.start_button.text() == "启动"
    assert not window.render_stop_button.isVisible()
    window._set_workflow_stage(
        "生成損傷分析報告",
        system_status="分析報告中",
        status_text="測試",
        overall_progress=50,
    )
    assert window.recognition_progress.value() == 50
    assert "生成損傷分析報告" in window.stage_status.text()
    window.close()


def test_gui_exposes_project_overview_before_workflow_start() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()

    assert window.project_overview_edit is not None
    assert window.project_overview_edit.placeholderText()
    assert window.project_overview_edit.maximumHeight() > 0
    assert window.project_overview_title.text() == "项目概括（可选）"
    assert window.project_overview_title.objectName() == "projectOverviewTitle"
    window.close()


def test_gui_exposes_markdown_report_and_plan_controls() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()

    assert window.open_report_word_button.text() == "打开分析报告"
    assert window.open_report_word_button.isEnabled() is False
    assert window.open_construction_plan_button.text() == "打开施工方案"
    assert window.open_construction_plan_button.isEnabled() is False
    assert not hasattr(window, "read_document_button")
    assert not hasattr(window, "choose_document_to_read")
    assert window.workflow_progress_label.text() == "处理进度"
    assert window.recognition_progress is window.workflow_progress_bar
    window.recognition_progress.setValue(42)
    assert window.workflow_progress_bar.value() == 42
    assert window.progress_ring.value() == 42
    window.close()


def test_docx_renderer_runs_only_after_human_confirmation() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    text = source.read_text(encoding="utf-8")

    assert text.count("_render_profile_docx(") == 2
    assert 'self._render_profile_docx("損傷分析報告", report.model_dump(mode="json"))' in text
    assert "construction_plan_done = Signal(object)" in text
    assert "build_local_fallback_construction_plan" in text


def test_render_gate_allows_confirmed_local_fallback_preview_but_keeps_review_gate() -> None:
    import runtime.damage_workflow_gui as gui

    source = Path(gui.__file__).resolve()
    text = source.read_text(encoding="utf-8")
    assert "def _construction_plan_has_local_fallback" in text
    assert "and not self._construction_plan_has_local_fallback(plan)" not in text
    assert "远端草稿未通过校验，本次仅生成本地保守预览" in text


def test_post_review_construction_generation_uses_dedicated_worker() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    text = source.read_text(encoding="utf-8")

    assert "class ConstructionPlanWorker(QThread):" in text
    assert "self.plan_worker: ConstructionPlanWorker | None = None" in text
    assert "self.plan_worker.generation_update.connect(self._on_generation_update)" in text
    assert "self.plan_worker.start()" in text
    build_plan_source = text[text.index("        def build_plan(self)"):text.index("        def render_repairs(self)")]
    assert "generate_plan_and_persist(" not in build_plan_source
    assert "build_local_fallback_construction_plan(" not in build_plan_source
    assert "_retrieve_profile_knowledge(" not in build_plan_source


def test_construction_worker_keeps_existing_wait_and_retry_contract() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    text = source.read_text(encoding="utf-8")
    worker_source = text[
        text.index("    class ConstructionPlanWorker(QThread):"):
        text.index("    class RepairRenderWorker(QThread):")
    ]

    assert "timeout=180.0" in worker_source
    assert "retries=2" in worker_source
    assert "def stop(" not in worker_source
    assert "cancelled = Signal" not in worker_source


def test_workflow_outputs_are_rooted_at_selected_input_folder() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    text = source.read_text(encoding="utf-8")

    assert 'self.output_dir = self.folder / "構件視界_分析輸出"' in text
    assert "default_output_root() / self.folder.name" not in text


def test_knowledge_import_survives_settings_save_and_toolbar_text_has_compact_style() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    text = source.read_text(encoding="utf-8")

    assert "dialog.finished.connect(stop_kb_worker)" not in text
    assert 'cancel_kb.setEnabled(True)' in text
    assert 'buttons.button(QDialogButtonBox.Save).setEnabled(False)' not in text
    assert 'worker.progress.connect(set_kb_status)' in text
    assert 'ocr = LocalOcrAdapter(prefer_gpu=True, device_id=0)' in text
    assert 'ocr=ocr' in text
    assert 'progress_detail = Signal(str, int, int, str)' in text
    assert 'worker.progress_detail.connect(progress)' in text
    assert 'if not settings_dialog_open():' in text
    assert 'self._knowledge_base_sync_pending = True' in text
    assert 'worker.finished.connect(sync_finished)' in text
    assert '生产 RAG 已停用' in text
    assert "#compactButton" in text
    assert "min-height:{px(32, 22)}px" in text
    assert "button.setMinimumWidth(button.sizeHint().width())" in text


def test_automatic_workflow_without_api_key_stops_at_human_review(tmp_path) -> None:
    import json
    import runtime.damage_workflow_gui as gui
    from runtime.settings_models import AppSettings

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    summary = {"project_overview": "离线回归", "results": []}
    (tmp_path / "batch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False),
        encoding="utf-8",
    )
    settings = AppSettings()
    settings.knowledge_base.root_dir = str(tmp_path / "knowledge_base")
    worker = gui.AutomaticWorkflowWorker(
        summary,
        tmp_path,
        {
            "responses_url": "https://api.openai.com/v1",
            "responses_key": "",
            "responses_model": "test-model",
            "fhl_url": "",
            "fhl_key": "",
        },
        False,
        settings,
    )
    failures: list[str] = []
    completed: list[object] = []
    worker.failed.connect(failures.append)
    worker.completed.connect(completed.append)

    worker.run()

    assert failures == []
    assert completed
    for filename in ("report.json", "report.md", "generation_manifest.json"):
        assert (tmp_path / filename).is_file()
    for filename in ("repair_plan.json", "construction_plan.json", "construction_plan.md"):
        assert not (tmp_path / filename).exists()
    assert completed[0]["awaiting_human_review"] is True
    manifest = json.loads((tmp_path / "generation_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["profiles"]) == {"損傷分析報告"}
    assert manifest["profiles"]["損傷分析報告"]["fallback"] is True


def test_detection_results_table_includes_screening_severity_column() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    assert [window.findings_table.horizontalHeaderItem(index).text() for index in range(6)] == ["编号", "文件名", "损伤类型", "置信度", "损伤等级", "状态"]
    window.close()


def test_main_workbench_matches_four_column_visual_layout() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()

    assert [window.image_list.horizontalHeaderItem(i).text() for i in range(4)] == ["状态", "文件名", "类型", "优先级"]
    assert window.actions_panel.objectName() == "actionsPanel"
    assert window.progress_ring.objectName() == "progressRing"
    assert window.workflow_progress_bar.objectName() == "workflowProgress"
    assert window.system_status.isVisible() is False
    assert window.current_file_title.text() == "当前文件"
    assert window.event_log_title.text() == "事件日志"
    assert window.event_log_title.objectName() == "panelTitle"
    assert window.event_log.sizePolicy().verticalPolicy().name == "Expanding"
    assert window.event_log.maximumHeight() == 16777215
    assert window.preview_mode.isVisible() is False
    assert window.result_filter.currentText() == "全部类型"
    window.close()


def test_current_file_panel_switches_between_image_and_live_generation_modes(tmp_path) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.output_dir = tmp_path
    window._begin_generation_preview("report")
    assert window.current_file_stack.currentIndex() == 1
    assert window.current_file_title.text() == "损伤分析报告"
    assert window.current_generation_file.text() == "report.md"
    window._on_generation_update("report", '{"executive_summary":"生成中"}')
    assert "生成中" in window.current_generation_content.toPlainText()
    (tmp_path / "report.md").write_text("# 分析报告\n已完成", encoding="utf-8")
    window._finish_generation_preview("report")
    assert "已完成" in window.current_generation_content.toPlainText()
    assert window.current_generation_status.text() == "已完成"
    window._update_current_file_details("image.jpg", {"image_path": str(tmp_path / "image.jpg")})
    assert window.current_file_stack.currentIndex() == 0
    window.close()


def test_streamed_generation_update_does_not_reenter_qt_event_loop() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window._begin_generation_preview("report")
    first_update_active = True
    dispatched_while_active: list[bool] = []

    def queued_update() -> None:
        dispatched_while_active.append(first_update_active)
        window._on_generation_update("report", "first second")

    QTimer.singleShot(0, queued_update)
    window._on_generation_update("report", "first")

    assert dispatched_while_active == []
    assert window.current_generation_content.toPlainText() == "first"

    first_update_active = False
    app.processEvents()
    assert dispatched_while_active == [False]
    assert window.current_generation_content.toPlainText() == "first second"
    window.close()


def test_production_gui_slots_do_not_pump_nested_qt_events() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    assert "QApplication.processEvents()" not in source.read_text(encoding="utf-8")


def test_manual_generation_paints_preview_without_dispatching_queued_events(monkeypatch) -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.show()
    app.processEvents()
    window._begin_generation_preview("report")

    viewport = window.current_generation_content.viewport()
    repaint = viewport.repaint
    repaint_calls: list[bool] = []

    def record_repaint() -> None:
        repaint_calls.append(True)
        repaint()

    monkeypatch.setattr(viewport, "repaint", record_repaint)
    queued: list[str] = []
    QTimer.singleShot(0, lambda: queued.append("next event"))

    window._on_manual_generation_update("report", "live preview")

    assert queued == []
    assert repaint_calls == [True]
    assert window.current_generation_content.toPlainText() == "live preview"
    app.processEvents()
    assert queued == ["next event"]
    window.close()


def test_data_source_action_controls_share_height() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.resize(1680, 980)
    window.show()
    app.processEvents()
    assert {button.height() for button in window.source_action_buttons} == {46}
    window.close()


def test_primary_action_and_render_toggle_keep_height_after_stop_repolish() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.resize(1680, 980)
    window.show()
    app.processEvents()
    assert window.start_button.height() == window.render_checkbox.height() == 48

    window.start_button.setText("停止")
    window.start_button.setObjectName("stopButton")
    window.start_button.style().unpolish(window.start_button)
    window.start_button.style().polish(window.start_button)
    window._sync_primary_action_heights()
    app.processEvents()
    assert window.start_button.height() == window.render_checkbox.height() == 48

    window.start_button.setText("启动")
    window.start_button.setObjectName("startButton")
    window.start_button.style().unpolish(window.start_button)
    window.start_button.style().polish(window.start_button)
    window._sync_primary_action_heights()
    app.processEvents()
    assert window.start_button.height() == window.render_checkbox.height() == 48
    window.close()


def test_adding_files_appends_to_existing_task_queue() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.image_list.addItems(["a.jpg", "b.jpg"])
    window._append_image_paths([Path("c.jpg")])
    assert [window.image_list.item(i).text() for i in range(window.image_list.count())] == ["a.jpg", "b.jpg", "c.jpg"]
    window.close()


def test_result_filter_hides_non_matching_rows() -> None:
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication, QTableWidgetItem

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    for index, kind in enumerate(("裂缝", "剥落")):
        window.findings_table.insertRow(index)
        for column, value in enumerate((str(index + 1), f"{index}.jpg", kind, "90%", "中等", "已完成")):
            window.findings_table.setItem(index, column, QTableWidgetItem(value))
    window._refresh_result_filter_options()
    window.result_filter.setCurrentText("裂缝")
    assert window.findings_table.isRowHidden(0) is False
    assert window.findings_table.isRowHidden(1) is True
    window.close()


def test_primary_action_switches_to_stop_for_a_running_worker() -> None:
    from PySide6.QtWidgets import QApplication
    import runtime.damage_workflow_gui as gui

    class RunningWorker:
        def isRunning(self) -> bool:
            return True

        def stop(self) -> None:
            self.stopped = True

        def wait(self, _milliseconds: int) -> bool:
            return True

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    worker = RunningWorker()
    window.worker = worker
    window.start_button.setText("停止")
    window.start_button.setObjectName("stopButton")
    window.toggle_workflow()
    assert worker.stopped is True
    assert window.workflow_stop_requested is True
    assert window.start_button.text() == "启动"
    assert window.start_button.objectName() == "startButton"
    window.close()


def test_primary_action_retries_failed_automatic_stage_without_recognition(tmp_path) -> None:
    from PySide6.QtWidgets import QApplication
    import runtime.damage_workflow_gui as gui

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.summary = {"results": []}
    window.output_dir = tmp_path
    window.workflow_retry_pending = True
    calls: list[str] = []
    window._continue_automatic_workflow = lambda: calls.append("automatic")
    window.start_recognition = lambda: calls.append("recognition")
    window.toggle_workflow()
    assert calls == ["automatic"]
    assert window.workflow_retry_pending is False
    window.close()


def test_yolo_device_selection_matches_torch_runtime() -> None:
    import torch
    import runtime.damage_workflow_gui as gui

    device, label = gui._resolve_yolo_device()
    assert device == ("0" if torch.cuda.is_available() else "cpu")
    assert ("啟用" in label) == torch.cuda.is_available()


def test_hud_queue_and_risk_metrics_refresh_from_live_results() -> None:
    from PySide6.QtWidgets import QApplication
    import runtime.damage_workflow_gui as gui

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.image_list.addItems(["a.jpg", "b.jpg", "c.jpg"])
    window.live_results = {
        "a.jpg": {
            "status": "success",
            "damage_findings": [{"screening_severity": {"level": "high"}}],
        },
        "b.jpg": {
            "status": "no_detection",
            "damage_findings": [],
        },
    }
    window._refresh_hud_metrics(processing=True)
    assert window.queue_count.text() == "2 / 3"
    assert "佇列總數：3" in window.queue_stats.text()
    assert "已完成：2" in window.queue_stats.text()
    assert "失敗：0" in window.queue_stats.text()
    assert window.finding_summary.text() == "已檢出 1 項損傷"
    assert "等待中 0" in window.progress_metrics.text()
    window.close()
