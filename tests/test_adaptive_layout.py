from __future__ import annotations

import pytest


SUPPORTED_VIEWPORTS = (
    (1024, 640),
    (1280, 720),
    (1366, 768),
    (1440, 900),
    (1680, 980),
    (1920, 1080),
    (2560, 1440),
)


def _window(width: int = 1680, height: int = 980):
    import runtime.damage_workflow_gui as gui

    if gui.GUI_IMPORT_ERROR is not None:
        return None, None
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    window.resize(width, height)
    window.show()
    for _ in range(3):
        app.processEvents()
    return app, window


def _rect_in_root(widget, root):
    from PySide6.QtCore import QRect

    origin = widget.mapTo(root, widget.rect().topLeft())
    return QRect(origin, widget.size())


def _major_panels(window):
    return [
        window._responsive_widgets[name]
        for name in (
            "queue",
            "preview_panel",
            "findings_panel",
            "project_panel",
            "document_panel",
            "status_panel",
            "current_panel",
            "log_panel",
        )
    ] + [window.actions_panel]


@pytest.mark.parametrize(("width", "height"), SUPPORTED_VIEWPORTS)
def test_supported_viewport_contains_complete_workbench_without_scrollbars(width: int, height: int) -> None:
    from PySide6.QtWidgets import QScrollArea

    app, window = _window(width, height)
    if window is None:
        return
    try:
        root = window.workbench_content
        assert window.centralWidget() is root
        assert window.workbench_scroll is None
        assert window.findChildren(QScrollArea) == []
        for panel in _major_panels(window):
            rect = _rect_in_root(panel, root)
            assert rect.width() > 0 and rect.height() > 0
            assert root.rect().contains(rect), (width, height, panel.objectName(), rect, root.rect())
        for control in (
            window.api_button,
            window.assistant_button,
            window.open_report_word_button,
            window.open_construction_plan_button,
            window.start_button,
            window.render_checkbox,
        ):
            assert control.sizeHint().width() <= control.width(), (width, height, control.text())
            assert control.sizeHint().height() <= control.height(), (width, height, control.text())
        assert window.workflow_progress_bar.sizeHint().width() <= window.workflow_progress_bar.width()
        assert window.workflow_progress_bar.sizeHint().height() <= window.workflow_progress_bar.height()
        assert all(button.sizeHint().width() <= button.width() for button in window.source_action_buttons)

        queue_rect = _rect_in_root(window._responsive_widgets["queue"], root)
        preview_rect = _rect_in_root(window._responsive_widgets["preview_panel"], root)
        findings_rect = _rect_in_root(window._responsive_widgets["findings_panel"], root)
        project_rect = _rect_in_root(window._responsive_widgets["project_panel"], root)
        document_rect = _rect_in_root(window._responsive_widgets["document_panel"], root)
        status_rect = _rect_in_root(window._responsive_widgets["status_panel"], root)

        assert queue_rect.right() < preview_rect.left() < findings_rect.left()
        assert max(queue_rect.bottom(), preview_rect.bottom(), findings_rect.bottom()) < project_rect.top()
        assert project_rect.bottom() < document_rect.top() < status_rect.top()
        assert window.project_overview_edit.height() > window.project_overview_title.height()
        assert window.project_overview_edit.geometry().bottom() <= window._responsive_widgets["project_panel"].contentsRect().bottom()
        assert window.event_log_title.geometry().top() == window.current_file_title.geometry().top()
        assert window.event_log.geometry().top() > window.event_log_title.geometry().bottom()
        assert window.event_log.geometry().bottom() <= window._responsive_widgets["log_panel"].contentsRect().bottom()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_workbench_preserves_established_panel_proportions() -> None:
    from PySide6.QtWidgets import QHBoxLayout

    app, window = _window()
    if window is None:
        return
    try:
        body = window.workbench_outer.itemAt(1).layout()
        assert isinstance(body, QHBoxLayout)
        assert [body.stretch(index) for index in range(3)] == [1, 3, 3]
        status_row = window.findChild(QHBoxLayout, "statusRow")
        assert status_row is not None
        assert [status_row.stretch(index) for index in range(4)] == [2, 4, 5, 3]

        queue_width = window._responsive_widgets["queue"].width()
        preview_width = window._responsive_widgets["preview_panel"].width()
        findings_width = window._responsive_widgets["findings_panel"].width()
        assert abs(preview_width - findings_width) <= 2
        assert preview_width > queue_width
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(("width", "height"), ((1024, 640), (1680, 980), (2560, 1440)))
def test_detection_result_columns_preserve_configured_proportions(
    width: int, height: int
) -> None:
    import runtime.damage_workflow_gui as gui

    app, window = _window(width, height)
    if window is None:
        return
    try:
        column_widths = [window.findings_table.columnWidth(index) for index in range(6)]
        total = sum(column_widths)
        assert total > 0
        assert abs(total - window.findings_table.viewport().width()) <= 2
        for actual, expected in zip(column_widths, gui.DETECTION_RESULT_COLUMN_PROPORTIONS):
            assert abs(actual / total - expected) <= 0.015
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_responsive_scale_grows_monotonically_and_updates_control_geometry() -> None:
    app, window = _window(1024, 640)
    if window is None:
        return
    try:
        compact_scale = window._layout_scale
        compact_height = window.start_button.height()
        window.resize(1680, 980)
        for _ in range(3):
            app.processEvents()
        standard_scale = window._layout_scale
        standard_height = window.start_button.height()
        window.resize(2560, 1440)
        for _ in range(3):
            app.processEvents()
        wide_scale = window._layout_scale
        wide_height = window.start_button.height()

        assert 0.60 <= compact_scale < standard_scale < wide_scale <= 1.08
        assert compact_height < standard_height <= wide_height
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_compact_source_tools_and_primary_actions_fit_their_containers() -> None:
    app, window = _window(1024, 640)
    if window is None:
        return
    try:
        assert all(button.text() == "" for button in window.source_action_buttons)
        assert all(button.toolTip() for button in window.source_action_buttons)
        assert window.render_checkbox.text() == "生成修复渲染图"
        assert window.render_checkbox.toolTip() == "生成修复渲染图"
        assert window.start_button.sizeHint().width() <= window.start_button.width()
        assert window.render_checkbox.sizeHint().width() <= window.render_checkbox.width()

        window.resize(1920, 1080)
        for _ in range(3):
            app.processEvents()
        assert all(button.text() == button.toolTip() for button in window.source_action_buttons)
        assert window.render_checkbox.text() == "生成修复渲染图"
        assert window.render_checkbox.text() == window.render_checkbox.toolTip()
        assert all(button.sizeHint().width() <= button.width() for button in window.source_action_buttons)
        assert window.render_checkbox.sizeHint().width() <= window.render_checkbox.width()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_queue_header_does_not_stretch_on_fullscreen_width() -> None:
    app, window = _window(2560, 1440)
    if window is None:
        return
    try:
        assert window.queue_header.height() <= 40
        assert window.queue_header.geometry().bottom() < window.image_list.geometry().top()
        assert window.queue_count.height() <= 32
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
