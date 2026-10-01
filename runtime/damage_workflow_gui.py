from __future__ import annotations

import json
import os
import sys
import ctypes
import copy
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from runtime.app_paths import (
    application_resource_root,
    resolve_user_path,
    user_config_path,
    user_knowledge_base_root,
)
from runtime.damage_repair_plan import DamageRepairPlanner
from runtime.fhl_repair_renderer import FhlRepairRenderer
from runtime.siliconflow_repair_renderer import SiliconFlowRepairRenderer
from runtime.generation_context import build_generation_context, render_reference_appendix
from runtime.knowledge_base import ActiveRagScopeAdapter, DocumentExtractionCancelled, KnowledgeBase, KnowledgeBaseSearchResult, LocalOcrAdapter, pipeline_search_with_scope
from runtime.rag_query import build_profile_query
from runtime.settings_models import AppSettings, PROFILE_NAMES
from runtime.settings_store import SettingsStore
from runtime.yolo_segmentation_runtime import DEFAULT_MODEL_PATH, YoloSegmentationRuntime
from runtime.rag_sync import active_document_ids, active_rag_snapshot, bootstrap_active_catalog, rebuild_and_activate, resolve_profile_folder_scope, synchronize_settings_scopes, user_facing_sync_error
from runtime.assistant.web_search import PublicWebSearchProvider, ResponsesWebSearchProvider


PROJECT_OVERVIEW_MAX_CHARS = 4000
SCREENING_SEVERITY_TOOLTIP = "影像损伤面积比例初筛等级，不是结构安全等级或可靠性鉴定结论。"
DETECTION_RESULT_COLUMN_PROPORTIONS = (0.09, 0.21, 0.26, 0.14, 0.16, 0.14)


def _screening_severity_label(value: Any) -> str:
    """Return the conservative, user-facing label for detection-stage severity."""
    normalized = str(value or "").strip().casefold()
    return {
        "undetermined": "待判定",
        "low": "轻微",
        "medium": "中等",
        "high": "严重",
    }.get(normalized, "待判定")


def _construction_evidence(
    summary: dict[str, Any],
    report: Any,
    repair_plan: dict[str, Any],
) -> dict[str, Any]:
    """Build JSON-compatible evidence for construction retrieval and prompting."""
    model_dump = getattr(report, "model_dump", None)
    report_payload = model_dump(mode="json") if callable(model_dump) else dict(report)
    return {
        "project_overview": summary.get("project_overview", ""),
        "summary": summary,
        "validated_report": report_payload,
        "deterministic_repair_plan": repair_plan,
    }


def _retrieve_profile_knowledge(
    kb_root: Path,
    profile: Any,
    knowledge_settings: Any,
    evidence: dict[str, Any],
) -> KnowledgeBaseSearchResult:
    """Retrieve only the profile-selected scope, with deterministic scoped fallback."""
    profile_document_ids = tuple(profile.knowledge_base_document_ids)
    profile_folder_ids = tuple(profile.knowledge_base_folder_ids)
    document_ids = (
        profile_document_ids
        if profile_document_ids
        else (() if profile_folder_ids else tuple(knowledge_settings.enabled_document_ids))
    )
    query = build_profile_query(getattr(profile, "name", "損傷分析報告"), evidence)
    return pipeline_search_with_scope(
        ActiveRagScopeAdapter(),
        query,
        document_ids=document_ids,
        folder_ids=profile_folder_ids,
        top_k=knowledge_settings.top_k,
        max_chars=knowledge_settings.max_context_chars,
    )


def _make_profile_knowledge_tool(
    kb_root: Path,
    profile: Any,
    knowledge_settings: Any,
    context: Any,
) -> Any:
    """Create the model-facing tool while enforcing the profile scope locally."""
    def search(query: str, top_k: int = 6) -> dict[str, Any]:
        result = pipeline_search_with_scope(
            ActiveRagScopeAdapter(),
            str(query or profile.name),
            document_ids=tuple(profile.knowledge_base_document_ids)
            or (() if profile.knowledge_base_folder_ids else tuple(knowledge_settings.enabled_document_ids)),
            folder_ids=tuple(profile.knowledge_base_folder_ids),
            top_k=max(1, min(int(top_k or knowledge_settings.top_k), 8)),
            max_chars=knowledge_settings.max_context_chars,
        )
        chunks = [
            {"chunk_id": c.chunk_id, "document_id": c.document_id, "location": c.location,
             "source_marker": c.source_marker, "score": c.score, "metadata": dict(c.metadata), "text": c.text}
            for c in result.chunks
        ]
        context.retrieved_chunks.extend(item for item in chunks if item not in context.retrieved_chunks)
        context.knowledge_base_used = bool(context.retrieved_chunks)
        context.knowledge_base_status = "used" if chunks else "unavailable"
        context.knowledge_base_retrieval_mode = result.retrieval_mode if chunks else "none"
        context.knowledge_base_scope_document_ids = tuple(result.scope_document_ids)
        context.anchors = [dict(item) for item in result.anchors]
        context.context_groups = [dict(item) for item in result.context_groups]
        audit = context.route_diagnostics.setdefault("ai_tool_rag", {})
        calls = audit.setdefault("knowledge_base_calls", [])
        calls.append({
            "query": str(query),
            "top_k": max(1, min(int(top_k or knowledge_settings.top_k), 8)),
            "result_count": len(chunks),
            "source_markers": [str(item.get("source_marker") or "") for item in chunks],
            "retrieval_mode": result.retrieval_mode,
        })
        return {"query": str(query), "chunks": chunks, "retrieval_mode": result.retrieval_mode,
                "scope_document_ids": list(result.scope_document_ids), "warnings": list(result.warnings)}
    return search


def _generation_web_search_provider(api_config: dict[str, str], model: str) -> Any:
    """Use the configured Responses tool when possible, otherwise public search."""
    key = str(api_config.get("responses_key") or "").strip()
    if key:
        return ResponsesWebSearchProvider(
            api_key=key,
            base_url=str(api_config.get("responses_url") or ""),
            model=str(model or api_config.get("responses_model") or ""),
        )
    return PublicWebSearchProvider()

try:
    if os.name == "nt":
        site_packages = Path(sys.prefix) / "Lib" / "site-packages"
        qt_dirs = [site_packages / "shiboken6", site_packages / "PySide6"]
        existing = [str(path) for path in qt_dirs if path.is_dir()]
        if existing:
            os.environ["PATH"] = ";".join(existing + [os.environ.get("PATH", "")])
            for directory in existing:
                try:
                    os.add_dll_directory(directory)
                except (AttributeError, FileNotFoundError, OSError):
                    pass
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        for name in ("icuuc.dll", "icuin.dll"):
            dll = system_root / "System32" / name
            if dll.is_file():
                try:
                    ctypes.WinDLL(str(dll))
                except OSError:
                    pass
    from PySide6.QtCore import QEvent, QRectF, QThread, Qt, Signal, QTimer
    from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen, QPixmap, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLayout,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QCheckBox,
        QComboBox,
        QProgressBar,
        QPlainTextEdit,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QStyle,
        QStackedWidget,
        QTabWidget,
        QTableWidget,
        QTableWidgetItem,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    GUI_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment-specific
    GUI_IMPORT_ERROR = exc


_UI_FONT_REGISTERED = False


def _register_ui_font() -> None:
    """Register a bundled system CJK font for Qt platforms without font discovery."""
    global _UI_FONT_REGISTERED
    if _UI_FONT_REGISTERED or GUI_IMPORT_ERROR is not None:
        return
    candidates = (
        Path(r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
    )
    for candidate in candidates:
        if candidate.is_file() and QFontDatabase.addApplicationFont(str(candidate)) >= 0:
            break
    _UI_FONT_REGISTERED = True


def _read_image_unicode(path: Path, cv2_module: Any) -> np.ndarray | None:
    """Read an image from a Windows path that may contain non-ASCII characters."""
    try:
        encoded = np.fromfile(str(path), dtype=np.uint8)
        if encoded.size:
            image = cv2_module.imdecode(encoded, cv2_module.IMREAD_COLOR)
            if image is not None:
                return image
    except (OSError, ValueError):
        pass
    # Keep a fallback for non-Windows environments and mocked OpenCV modules.
    return cv2_module.imread(str(path), cv2_module.IMREAD_COLOR)


def _write_image_unicode(path: Path, image: np.ndarray, cv2_module: Any) -> None:
    """Write an image using encoded bytes so Unicode output paths work on Windows."""
    suffix = path.suffix.lower() or ".png"
    ok, encoded = cv2_module.imencode(suffix, image)
    if not ok:
        raise OSError(f"無法編碼輸出影像：{path}")
    path.write_bytes(encoded.tobytes())


def _load_pixmap_unicode(path: Path) -> Any:
    """Load a preview pixmap through bytes so Chinese paths work consistently."""
    pixmap = QPixmap()
    try:
        pixmap.loadFromData(path.read_bytes())
    except OSError:
        return pixmap
    return pixmap


def _simplify_detection_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Keep report-safe recognition fields plus deterministic GUI screening data."""
    screening = finding.get("screening_severity")
    return {
        "index": int(finding.get("index", 0)),
        "class_id": int(finding.get("class_id", -1)),
        "class_name": str(finding.get("class_name", "未知损伤")),
        "score": float(finding.get("score", 0.0) or 0.0),
        "detection_confidence": float(
            finding.get("detection_confidence", finding.get("score", 0.0)) or 0.0
        ),
        "screening_severity": dict(screening) if isinstance(screening, dict) else {},
    }


def _report_image_slots_from_summary(summary: dict[str, Any] | None) -> dict[str, str | None]:
    """Choose original and recognition overlay; no measurement visualization is produced."""
    empty = {"image_surface": None, "image_mask": None, "image_measurement": None}
    if not summary:
        return empty
    for result in summary.get("results", []):
        if not isinstance(result, dict) or result.get("status") != "success":
            continue
        slots = {
            "image_surface": str(result.get("image_path") or "") or None,
            "image_mask": str(result.get("overlay_path") or "") or None,
            "image_measurement": None,
        }
        if slots["image_surface"] and slots["image_mask"]:
            return slots
    return empty


def read_document_preview(path: str | Path) -> tuple[str, str]:
    """Extract selectable text from supported local report documents without editing them."""
    source = Path(path)
    if source.suffix.lower() not in {".docx", ".pdf"}:
        raise ValueError("仅支持读取 DOCX 或 PDF 文件")
    from runtime.knowledge_base import extract_document

    sections = extract_document(source)
    text = "\n\n".join(f"[{location}]\n{content}" for location, content in sections)
    return str(source), text or "（文件中没有可提取的文字）"


def _resolve_yolo_device() -> tuple[str, str]:
    """Select CUDA when available and return a user-facing verification string."""
    try:
        import torch

        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            name = torch.cuda.get_device_name(0)
            return "0", f"GPU 加速：啟用（{name}）"
    except Exception:
        pass
    return "cpu", "GPU 加速：未啟用（CPU）"


def _mask_secret(value: str) -> str:
    value = str(value or "")
    if len(value) <= 8:
        return "未設定" if not value else "已設定"
    return f"{value[:4]}…{value[-4:]}"


def _format_report_api_error(exc: Exception, *, base_url: str, model: str, api_key: str) -> str:
    """Turn SDK errors into actionable Traditional Chinese guidance without leaking keys."""
    error_name = type(exc).__name__
    detail = str(exc)
    lowered = detail.lower()
    key_hint = _mask_secret(api_key)
    if error_name == "AuthenticationError" or "401" in detail or "invalid_api_key" in lowered:
        return (
            "Responses API 認證失敗（HTTP 401）。\n"
            f"目前端點：{base_url}\n"
            f"目前模型：{model}\n"
            f"目前 Key：{key_hint}\n\n"
            "請確認此 Key 是文字模型/Responses API 的有效密鑰；FHL Image Gen 的 sk-… Key "
            "只保證用於圖片生成，不能直接當作報告 API Key。若使用 OpenAI，請填入 "
            "https://api.openai.com/v1 及對應的 sk-… Key。"
        )
    if "protocol_not_supported" in lowered or "does not support chat completions" in lowered:
        return (
            "所选模型不支持当前调用协议。\n"
            f"端点：{base_url}\n模型：{model}\n\n"
            "当前 GUI 对兼容端点使用 Chat Completions 结构化输出；请改用支持 Chat Completions 的文字模型，"
            "或使用同时允许标准 Responses API 客户端访问的端点。"
        )
    if error_name in {"NotFoundError", "BadRequestError"} or "model" in lowered:
        return (
            "Responses API 請求被拒絕，可能是模型或端點不支援。\n"
            f"端點：{base_url}\n模型：{model}\n\n"
            "请在设置中改用该服务提供的文字模型名称；FHL Image Gen 模型不能用来生成工程报告。"
        )
    if "timeout" in lowered or "timed out" in lowered or "apitimeouterror" in lowered:
        return (
            "Responses API 請求逾時。\n"
            f"端點：{base_url}\n模型：{model}\n\n"
            "報告內容較大或模型回應較慢，請稍後重試；程式已使用較長的請求逾時時間。"
        )
    if "internalservererror" in lowered or "http 5" in lowered or "http 429" in lowered:
        return (
            "报告服务暂时不可用，程序已完成自动重试。\n"
            f"端点：{base_url}\n模型：{model}\n"
            f"错误摘要：{detail}\n\n"
            "损伤识别结果已保留，请稍后重试；再次点击“启动”可重试报告阶段。"
        )
    return f"Responses API 請求失敗：{error_name}\n{detail}"


STAGES = ("損傷識別", "損傷分析報告", "修復施工方案", "修復後渲染圖")
STAGE_HELP = (
    "選擇包含損傷圖片的資料夾，執行 YOLO 分割識別。",
    "彙整所有圖片的損傷證據，生成工程分析報告。",
    "依損傷類型建立修復方法、施工假設與複核要點。",
    "使用 FHL Images API 為每張原圖生成修復後視覺化渲染圖。",
)


if GUI_IMPORT_ERROR is None:

    class ProportionalTableWidget(QTableWidget):
        """Keep table columns at stable proportions of the visible viewport."""

        def __init__(self, proportions: tuple[float, ...], parent: QWidget | None = None) -> None:
            if not proportions or any(value <= 0 for value in proportions):
                raise ValueError("column proportions must be positive")
            total = float(sum(proportions))
            if total <= 0:
                raise ValueError("column proportions must have a positive sum")
            self._column_proportions = tuple(float(value) / total for value in proportions)
            super().__init__(0, len(self._column_proportions), parent)
            header = self.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.Fixed)
            header.setStretchLastSection(False)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.viewport().installEventFilter(self)

        @property
        def column_proportions(self) -> tuple[float, ...]:
            return self._column_proportions

        def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt override
            if watched is self.viewport() and event.type() == QEvent.Resize:
                self._apply_column_proportions(event.size().width())
            return super().eventFilter(watched, event)

        def _apply_column_proportions(self, viewport_width: int | None = None) -> None:
            available = max(0, int(viewport_width if viewport_width is not None else self.viewport().width()))
            if available <= 0:
                return
            assigned = 0
            last_column = len(self._column_proportions) - 1
            for column, proportion in enumerate(self._column_proportions):
                width = available - assigned if column == last_column else round(available * proportion)
                self.setColumnWidth(column, max(1, width))
                assigned += width

    class TaskQueueTable(QTableWidget):
        """Visual task queue with QListWidget-compatible helpers used by the workflow."""

        currentTextChanged = Signal(str)

        def __init__(self) -> None:
            super().__init__(0, 4)
            self.setHorizontalHeaderLabels(["状态", "文件名", "类型", "优先级"])
            self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
            self.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.setSelectionMode(QAbstractItemView.SingleSelection)
            self.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.itemSelectionChanged.connect(self._emit_current_text)

        def _emit_current_text(self) -> None:
            item = super().item(self.currentRow(), 1) if self.currentRow() >= 0 else None
            self.currentTextChanged.emit(item.text() if item is not None else "")

        def addItems(self, names: list[str]) -> None:  # noqa: N802 - Qt compatibility API
            for name in names:
                self.add_task(str(name))

        def add_task(self, name: str, *, status: str = "等待中", priority: str = "—") -> None:
            row = self.rowCount()
            self.insertRow(row)
            suffix = Path(name).suffix.lower().lstrip(".").upper() or "—"
            values = (status, name, suffix, priority)
            for column, value in enumerate(values):
                self.setItem(row, column, QTableWidgetItem(value))
            self._style_status(row, status)

        def clear(self) -> None:  # noqa: A003 - Qt compatibility API
            self.setRowCount(0)

        def count(self) -> int:
            return self.rowCount()

        def item(self, row: int, column: int | None = None) -> QTableWidgetItem | None:  # noqa: A003
            return super().item(row, 1 if column is None else column)

        def setCurrentRow(self, row: int) -> None:  # noqa: N802 - QListWidget compatibility
            self.setCurrentCell(row, 1)

        def update_task(self, name: str, *, status: str, priority: str | None = None) -> None:
            for row in range(self.rowCount()):
                name_item = super().item(row, 1)
                if name_item is None or name_item.text() != name:
                    continue
                self.setItem(row, 0, QTableWidgetItem(status))
                if priority is not None:
                    self.setItem(row, 3, QTableWidgetItem(priority))
                self._style_status(row, status)
                return

        def _style_status(self, row: int, status: str) -> None:
            item = super().item(row, 0)
            if item is None:
                return
            colors = {"已完成": "#35dfa0", "检测中": "#24d5ee", "异常": "#ff9e2f", "等待中": "#9cb8c7"}
            item.setForeground(QColor(colors.get(status, "#dcecf5")))


    class CircularProgressWidget(QWidget):
        """Compact circular progress indicator used in the bottom status panel."""

        def __init__(self) -> None:
            super().__init__()
            self._value = 0
            self._ui_scale = 1.0
            self.setObjectName("progressRing")
            self.setMinimumSize(112, 112)

        def set_ui_scale(self, scale: float) -> None:
            self._ui_scale = max(0.5, float(scale))
            side = max(68, round(112 * self._ui_scale))
            self.setFixedSize(side, side)
            self.update()

        def setRange(self, _minimum: int, _maximum: int) -> None:  # noqa: N802 - QProgressBar compatibility
            return

        def setValue(self, value: int) -> None:  # noqa: N802 - QProgressBar compatibility
            self._value = max(0, min(100, int(value)))
            self.update()

        def value(self) -> int:
            return self._value

        def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            inset = max(8, round(16 * self._ui_scale))
            side = min(self.width(), self.height()) - inset
            rect = self.rect().center()
            box = QRectF(rect.x() - side / 2, rect.y() - side / 2, side, side)
            pen_width = max(4, round(8 * self._ui_scale))
            painter.setPen(QPen(QColor("#183b53"), pen_width, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(box, 0, 360 * 16)
            painter.setPen(QPen(QColor("#24d5ee"), pen_width, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(box, 90 * 16, -int(self._value * 360 * 16 / 100))
            painter.setPen(QColor("#24d5ee"))
            font = painter.font()
            font.setPointSize(max(10, round(18 * self._ui_scale)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, f"{self._value}%")
            painter.end()

    class RecognitionWorker(QThread):
        progress = Signal(int, str)
        image_done = Signal(str, object)
        completed = Signal(object)
        failed = Signal(str)

        def __init__(self, folder: Path, output_dir: Path, model_path: str, project_overview: str = "", image_paths: list[Path] | None = None) -> None:
            super().__init__()
            self.folder = folder
            self.output_dir = output_dir
            self.model_path = model_path
            self.project_overview = project_overview
            self.image_paths = list(image_paths) if image_paths else None
            self.stop_requested = False

        def stop(self) -> None:
            self.stop_requested = True

        def run(self) -> None:
            started_at = datetime.now().astimezone().isoformat()
            summary: dict[str, Any] = {
                "run_id": str(uuid.uuid4()),
                "source_folder": str(self.folder),
                "output_dir": str(self.output_dir),
                "model_path": self.model_path,
                "started_at": started_at,
                "cancelled": False,
                "project_overview": self.project_overview,
                "results": [],
            }
            try:
                paths = sorted(
                    self.image_paths
                    if self.image_paths is not None
                    else [p for p in self.folder.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}],
                    key=lambda p: p.name.casefold(),
                )
                if not paths:
                    raise ValueError("資料夾內沒有支援的損傷圖片")
                self.output_dir.mkdir(parents=True, exist_ok=True)
                import cv2

                model_path = Path(self.model_path)
                if not model_path.is_absolute():
                    model_path = application_resource_root() / model_path
                if not model_path.is_file():
                    raise FileNotFoundError(f"找不到識別模型：{model_path}")
                summary["resolved_model_path"] = str(model_path)
                device, device_status = _resolve_yolo_device()
                summary["inference_device"] = device
                summary["gpu_acceleration"] = device != "cpu"
                summary["device_status"] = device_status
                try:
                    runtime = YoloSegmentationRuntime(model_path=model_path, device=device)
                    ensure_model = getattr(runtime, "_ensure_model", None)
                    if callable(ensure_model):
                        model = ensure_model()
                        from runtime.bundled_model import validate_segmentation_model_contract

                        validate_segmentation_model_contract(model)
                except Exception as exc:
                    if device == "cpu":
                        raise
                    # CUDA can be visible but unusable (driver mismatch,
                    # insufficient memory, or a broken torch build).  Recreate
                    # the runtime on CPU so one failed initialization does not
                    # abort the entire desktop workflow.
                    try:
                        import torch

                        empty_cache = getattr(torch.cuda, "empty_cache", None)
                        if callable(empty_cache):
                            empty_cache()
                    except Exception:
                        pass
                    device = "cpu"
                    summary["inference_device"] = device
                    summary["gpu_acceleration"] = False
                    summary["device_status"] = f"GPU 初始化失败，已回退 CPU（{type(exc).__name__}）"
                    runtime = YoloSegmentationRuntime(model_path=model_path, device=device)
                    ensure_model = getattr(runtime, "_ensure_model", None)
                    if callable(ensure_model):
                        model = ensure_model()
                        from runtime.bundled_model import validate_segmentation_model_contract

                        validate_segmentation_model_contract(model)
                results: list[dict[str, Any]] = []
                for index, path in enumerate(paths, start=1):
                    if self.stop_requested:
                        break
                    image = _read_image_unicode(path, cv2)
                    if image is None:
                        result = {
                            "image_name": path.name,
                            "image_path": str(path),
                            "status": "failed",
                            "error": f"圖片讀取失敗：{path}",
                            "damage_findings": [],
                        }
                        results.append(result)
                        self.image_done.emit(path.name, result)
                        self.progress.emit(int(index * 100 / len(paths)), f"已完成 {index}/{len(paths)} 張圖片")
                        continue
                    try:
                        inference = runtime.infer_inspection(image)
                        overlay_path = self.output_dir / f"{path.stem}__識別覆蓋{path.suffix.lower()}"
                        _write_image_unicode(overlay_path, runtime._render_result(image, inference), cv2)
                        result = {
                            "image_name": path.name,
                            "image_path": str(path),
                            "overlay_path": str(overlay_path),
                            "status": "success" if inference["success"] else "no_detection",
                            "damage_findings": [
                                _simplify_detection_finding(finding)
                                for finding in inference.get("damage_findings", [])
                                if isinstance(finding, dict)
                            ],
                        }
                    except Exception as exc:
                        result = {
                            "image_name": path.name,
                            "image_path": str(path),
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "damage_findings": [],
                        }
                    results.append(result)
                    self.image_done.emit(path.name, result)
                    self.progress.emit(int(index * 100 / len(paths)), f"已完成 {index}/{len(paths)} 張圖片")
                summary["cancelled"] = self.stop_requested
                summary["results"] = results
                summary["status"] = "cancelled" if self.stop_requested else "completed"
                summary["finished_at"] = datetime.now().astimezone().isoformat()
                summary_path = self.output_dir / "batch_summary.json"
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                try:
                    from runtime.assistant.context import record_detection_snapshot

                    record_detection_snapshot(summary_path, include_artifacts=False)
                except Exception:
                    pass
                self.completed.emit({"summary": summary, "summary_path": str(summary_path)})
            except Exception as exc:
                summary["status"] = "failed"
                summary["error"] = f"{type(exc).__name__}: {exc}"
                summary["finished_at"] = datetime.now().astimezone().isoformat()
                try:
                    self.output_dir.mkdir(parents=True, exist_ok=True)
                    (self.output_dir / "batch_summary.json").write_text(
                        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    try:
                        from runtime.assistant.context import record_detection_snapshot

                        record_detection_snapshot(self.output_dir / "batch_summary.json", include_artifacts=False)
                    except Exception:
                        pass
                except OSError:
                    pass
                self.failed.emit(f"批次識別失敗：{summary['error']}")


    class KnowledgeBaseImportWorker(QThread):
        """Index selected reference files without blocking the settings dialog."""

        completed = Signal(object)
        failed = Signal(str)
        progress = Signal(str, int, int)
        progress_detail = Signal(str, int, int, str)
        cancelled = Signal(str)

        def __init__(self, knowledge_base: KnowledgeBase, paths: list[str], *, folder_id: str = "", chunk_size: int, chunk_overlap: int) -> None:
            super().__init__()
            self.knowledge_base = knowledge_base
            self.paths = paths
            self.folder_id = folder_id
            self.chunk_size = chunk_size
            self.chunk_overlap = chunk_overlap
            self.stop_requested = False

        def stop(self) -> None:
            self.stop_requested = True

        def run(self) -> None:
            records = []
            ocr = LocalOcrAdapter(prefer_gpu=True, device_id=0)
            try:
                for path in self.paths:
                    if self.stop_requested:
                        raise DocumentExtractionCancelled("知识库导入已取消")
                    self.progress.emit(str(path), 0, 0)
                    self.progress_detail.emit(str(path), 0, 0, ocr.backend_label)
                    records.append(
                        self.knowledge_base.add_document(
                            path,
                            folder_id=self.folder_id,
                            chunk_size=self.chunk_size,
                            chunk_overlap=self.chunk_overlap,
                            ocr=ocr,
                            progress=lambda page, total, current_path=str(path): (
                                self.progress.emit(current_path, page, total),
                                self.progress_detail.emit(current_path, page, total, ocr.backend_label),
                            ),
                            should_stop=lambda: self.stop_requested,
                        )
                    )
                self.completed.emit(records)
            except DocumentExtractionCancelled as exc:
                self.cancelled.emit(str(exc))
            except Exception as exc:
                self.failed.emit(f"知识库导入失败：{type(exc).__name__}: {exc}")


    class KnowledgeBaseProductionSyncWorker(QThread):
        """Rebuild and activate production RAG after a GUI catalog import."""

        completed = Signal(object)
        failed = Signal(str)
        progress = Signal(str)

        def __init__(self, kb_root: Path) -> None:
            super().__init__()
            self.kb_root = Path(kb_root)

        def run(self) -> None:
            try:
                self.completed.emit(rebuild_and_activate(self.kb_root, progress=self.progress.emit))
            except Exception as exc:
                self.failed.emit(user_facing_sync_error(exc))


    class AutomaticWorkflowWorker(QThread):
        stage = Signal(str, str, int)
        progress = Signal(int, str)
        generation_update = Signal(str, str)
        report_done = Signal(object)
        plan_done = Signal(object)
        construction_plan_done = Signal(object)
        render_item = Signal(object)
        render_done = Signal(object)
        completed = Signal(object)
        cancelled = Signal(str)
        failed = Signal(str)

        def __init__(
            self,
            summary: dict[str, Any],
            output_dir: Path,
            api_config: dict[str, str],
            render_enabled: bool,
            settings: AppSettings | None = None,
        ) -> None:
            super().__init__()
            self.summary = summary
            self.output_dir = output_dir
            self.api_config = api_config
            self.render_enabled = render_enabled
            self.settings = settings or AppSettings()
            self.stop_requested = False

        def stop(self) -> None:
            self.stop_requested = True

        def _cancel_if_requested(self) -> bool:
            if self.stop_requested:
                self.cancelled.emit("已停止當前流程，已完成的報告與方案結果予以保留。")
                return True
            return False

        def run(self) -> None:
            try:
                if self._cancel_if_requested():
                    return
                self.stage.emit("生成損傷分析報告", "分析報告中", 25)
                from runtime.responses_damage_report import (
                    ResponsesReportService,
                    build_local_fallback_report_from_summary,
                    normalize_responses_base_url,
                )

                snapshot_id, snapshot = self.settings.snapshot()
                profile = snapshot.generation_profiles["損傷分析報告"]
                kb_root = resolve_user_path(snapshot.knowledge_base.root_dir, default=user_knowledge_base_root())
                report_kb_error = ""
                try:
                    report_kb_result = (
                        KnowledgeBaseSearchResult((), scope_available=True, retrieval_mode="ai_tool")
                        if snapshot.knowledge_base.ai_tool_rag
                        else _retrieve_profile_knowledge(kb_root, profile, snapshot.knowledge_base, self.summary)
                    )
                    chunks = list(report_kb_result.chunks)
                    if report_kb_result.used_scoped_fallback:
                        self.progress.emit(25, "报告知识库词法未命中，已在損傷分析報告选定范围内使用代表片段")
                    if not report_kb_result.scope_available:
                        report_kb_error = "損傷分析報告 profile 未找到可用的已索引知识库片段，请先在设置中导入并选择文档"
                        self.progress.emit(25, f"{report_kb_error}；知识库不可用，将仅依据结构化证据继续调用 AI")
                except Exception as exc:
                    chunks = []
                    report_kb_result = KnowledgeBaseSearchResult((), scope_available=False)
                    report_kb_error = f"报告知识库检索失败：{type(exc).__name__}: {exc}"
                    self.progress.emit(25, report_kb_error)
                generation_context = build_generation_context(
                    profile,
                    self.summary,
                    settings_snapshot_id=snapshot_id,
                    retrieved_chunks=chunks,
                    retrieval_mode=report_kb_result.retrieval_mode,
                    knowledge_base_scope_document_ids=report_kb_result.scope_document_ids,
                    knowledge_base_status=("unavailable" if report_kb_error else "used"),
                    anchors=report_kb_result.anchors,
                    context_groups=report_kb_result.context_groups,
                    retrieval_warnings=report_kb_result.warnings,
                    route_diagnostics=report_kb_result.route_diagnostics,
                    retrieval_query=build_profile_query(profile.name, self.summary),
                    web_search_provider=_generation_web_search_provider(self.api_config, profile.model),
                )
                report_kb_tool = _make_profile_knowledge_tool(kb_root, profile, snapshot.knowledge_base, generation_context)
                responses_key = self.api_config.get("responses_key", "").strip()
                try:
                    if not responses_key:
                        raise ValueError("未配置 Responses API Key")
                    service = ResponsesReportService(
                        api_key=responses_key,
                        base_url=normalize_responses_base_url(self.api_config["responses_url"]),
                        model=profile.model or self.api_config["responses_model"],
                        timeout=180.0,
                        retries=2,
                        on_text_update=lambda text: self.generation_update.emit("report", text),
                        knowledge_base_tool=report_kb_tool,
                        ai_tool_rag=snapshot.knowledge_base.ai_tool_rag,
                        max_knowledge_base_tool_calls=snapshot.knowledge_base.max_tool_calls,
                    )
                    report = service.generate_report_from_summary(
                        self.output_dir / "batch_summary.json",
                        output_dir=self.output_dir,
                        generation_context=generation_context,
                    )
                except Exception as exc:
                    fallback_reason = str(exc).replace(responses_key, "<redacted>") if responses_key else str(exc)
                    report = build_local_fallback_report_from_summary(
                        self.output_dir / "batch_summary.json",
                        output_dir=self.output_dir,
                        reason=fallback_reason,
                        generation_context=generation_context,
                    )
                    self.progress.emit(50, "远程报告服务不可用，已生成本地证据报告并继续流程")
                self.report_done.emit(report)
                self.progress.emit(50, f"損傷分析報告完成（知识库参考 {len(chunks)} 段，检索模式 {generation_context.knowledge_base_retrieval_mode}，来源 {generation_context.answer_source_mode}）")
                self.completed.emit(
                    {
                        "awaiting_human_review": True,
                        "report_path": str(self.output_dir / "report.json"),
                    }
                )
            except Exception as exc:
                self.failed.emit(f"自動流程失敗：{type(exc).__name__}: {exc}")


    class ConstructionPlanWorker(QThread):
        """Run the complete post-review construction stage off the GUI thread."""

        stage = Signal(str, str, int)
        progress = Signal(int, str)
        generation_update = Signal(str, str)
        plan_done = Signal(object)
        construction_plan_done = Signal(object, str, bool)
        failed = Signal(str)

        def __init__(
            self,
            summary: dict[str, Any],
            output_dir: Path,
            api_config: dict[str, str],
            settings: AppSettings,
        ) -> None:
            super().__init__()
            self.summary = summary
            self.output_dir = Path(output_dir)
            self.api_config = dict(api_config)
            self.settings = settings

        def _transport_status(self, payload: dict[str, Any]) -> None:
            event = str(payload.get("event", ""))
            if event == "attempt_started":
                attempt = int(payload.get("attempt", 1))
                maximum = int(payload.get("max_attempts", 1))
                self.progress.emit(65, f"正在請求施工方案：第 {attempt}/{maximum} 次嘗試")
            elif event == "route_selected":
                route = {
                    "responses": "Responses 流式接口",
                    "chat_completions": "兼容 Chat 流式接口",
                }.get(str(payload.get("route", "")), "結構化生成接口")
                self.progress.emit(65, f"施工方案遠程生成中：{route}")
            elif event == "retry_scheduled":
                next_attempt = int(payload.get("next_attempt", 1))
                maximum = int(payload.get("max_attempts", 1))
                self.progress.emit(65, f"遠程返回中斷，準備第 {next_attempt}/{maximum} 次嘗試")
            elif event == "request_completed":
                self.progress.emit(70, "遠程草稿接收完成，正在本地校驗與組裝")

        def run(self) -> None:
            responses_key = str(self.api_config.get("responses_key", "") or "").strip()
            try:
                from runtime.responses_construction_plan import (
                    ResponsesConstructionPlanService,
                    build_local_fallback_construction_plan,
                    classify_construction_fallback_reason,
                )
                from runtime.responses_damage_report import (
                    load_confirmed_report,
                    normalize_responses_base_url,
                )

                self.stage.emit("建立修復施工方案", "施工方案準備中", 55)
                report = load_confirmed_report(self.output_dir / "report.json")
                planner = DamageRepairPlanner()
                plan_summary = planner.build_summary(
                    self.summary.get("results", []),
                    report=report,
                )
                planner.save_summary(plan_summary, self.output_dir / "repair_plan.json")
                self.plan_done.emit(plan_summary)
                self.progress.emit(58, "確定性修復工法已建立，正在檢索施工參考")

                snapshot_id, snapshot = self.settings.snapshot()
                profile = snapshot.generation_profiles["施工方案"]
                evidence = _construction_evidence(self.summary, report, plan_summary)
                kb_root = resolve_user_path(
                    snapshot.knowledge_base.root_dir,
                    default=user_knowledge_base_root(),
                )
                try:
                    plan_kb_result = (
                        KnowledgeBaseSearchResult((), scope_available=True, retrieval_mode="ai_tool")
                        if snapshot.knowledge_base.ai_tool_rag
                        else _retrieve_profile_knowledge(kb_root, profile, snapshot.knowledge_base, evidence)
                    )
                    chunks = list(plan_kb_result.chunks)
                    plan_kb_error = ""
                    if plan_kb_result.used_scoped_fallback:
                        self.progress.emit(60, "施工知識庫詞法未命中，已在選定範圍使用代表片段")
                    if not plan_kb_result.scope_available:
                        plan_kb_error = "施工方案 profile 未找到可用的已索引知識庫片段"
                        self.progress.emit(60, f"{plan_kb_error}；將僅依據結構化證據繼續調用 AI")
                except Exception as exc:
                    chunks = []
                    plan_kb_result = KnowledgeBaseSearchResult((), scope_available=False)
                    plan_kb_error = f"施工知識庫檢索失敗：{type(exc).__name__}"
                    self.progress.emit(60, f"{plan_kb_error}；將僅依據結構化證據繼續調用 AI")

                generation_context = build_generation_context(
                    profile,
                    evidence,
                    settings_snapshot_id=snapshot_id,
                    retrieved_chunks=chunks,
                    retrieval_mode=plan_kb_result.retrieval_mode,
                    anchors=plan_kb_result.anchors,
                    context_groups=plan_kb_result.context_groups,
                    retrieval_warnings=plan_kb_result.warnings,
                    route_diagnostics=plan_kb_result.route_diagnostics,
                    retrieval_query=build_profile_query(profile.name, evidence),
                    web_search_provider=_generation_web_search_provider(self.api_config, profile.model),
                    knowledge_base_scope_document_ids=plan_kb_result.scope_document_ids,
                    knowledge_base_status=("unavailable" if plan_kb_error else "used"),
                )
                plan_kb_tool = _make_profile_knowledge_tool(kb_root, profile, snapshot.knowledge_base, generation_context)
                fallback = False
                try:
                    if not responses_key:
                        raise ValueError("未配置 Responses API Key")
                    self.progress.emit(62, "施工參考與證據上下文已就緒，正在連接遠程模型")
                    service = ResponsesConstructionPlanService(
                        api_key=responses_key,
                        base_url=normalize_responses_base_url(self.api_config["responses_url"]),
                        model=profile.model or self.api_config["responses_model"],
                        timeout=180.0,
                        retries=2,
                        on_text_update=lambda text: self.generation_update.emit(
                            "construction_plan", text
                        ),
                        on_status_update=self._transport_status,
                        knowledge_base_tool=plan_kb_tool,
                        ai_tool_rag=snapshot.knowledge_base.ai_tool_rag,
                        max_knowledge_base_tool_calls=snapshot.knowledge_base.max_tool_calls,
                    )
                    construction_plan = service.generate_plan_and_persist(
                        report=report,
                        repair_plan=plan_summary,
                        output_dir=self.output_dir,
                        generation_context=generation_context,
                    )
                except Exception as exc:
                    fallback = True
                    fallback_reason = (
                        str(exc).replace(responses_key, "<redacted>")
                        if responses_key
                        else str(exc)
                    )
                    diagnostic = classify_construction_fallback_reason(fallback_reason)
                    self.progress.emit(70, diagnostic.progress_message)
                    construction_plan = build_local_fallback_construction_plan(
                        report=report,
                        repair_plan=plan_summary,
                        output_dir=self.output_dir,
                        reason=fallback_reason,
                        generation_context=generation_context,
                    )
                self.progress.emit(75, "施工方案草案已持久化，正在更新界面")
                self.construction_plan_done.emit(
                    construction_plan,
                    generation_context.answer_source_mode,
                    fallback,
                )
            except Exception as exc:
                message = str(exc).replace(responses_key, "<redacted>") if responses_key else str(exc)
                self.failed.emit(f"{type(exc).__name__}: {message}")


    class RepairRenderWorker(QThread):
        """Run repair rendering without blocking the Qt event loop."""

        item_done = Signal(object)
        completed = Signal(object)
        cancelled = Signal(object)
        failed = Signal(str)

        def __init__(
            self,
            items: list[tuple[str | Path, str]],
            output_dir: Path,
            api_config: dict[str, str],
            base_prompt: str,
        ) -> None:
            super().__init__()
            self.items = list(items)
            self.output_dir = Path(output_dir)
            self.api_config = dict(api_config)
            self.base_prompt = str(base_prompt)
            self.stop_requested = False

        def stop(self) -> None:
            self.stop_requested = True

        def run(self) -> None:
            try:
                provider = str(self.api_config.get("repair_render_provider") or "fhl").strip().casefold()
                if provider == "siliconflow":
                    renderer = SiliconFlowRepairRenderer(
                        output_dir=self.output_dir,
                        api_key=self.api_config.get("siliconflow_key") or None,
                        base_url=self.api_config.get("siliconflow_url") or None,
                        model=self.api_config.get("siliconflow_model") or None,
                        base_prompt=self.base_prompt,
                    )
                elif provider == "fhl":
                    renderer = FhlRepairRenderer(
                        output_dir=self.output_dir,
                        api_key=self.api_config.get("fhl_key") or None,
                        api_url=self.api_config.get("fhl_url") or None,
                        base_prompt=self.base_prompt,
                    )
                else:
                    raise ValueError(f"不支持的修复渲染平台：{provider}")
                results = renderer.render_many(
                    self.items,
                    should_stop=lambda: self.stop_requested,
                    on_result=self.item_done.emit,
                )
                payload = {"results": results}
                if self.stop_requested:
                    self.cancelled.emit(payload)
                else:
                    self.completed.emit(payload)
            except Exception as exc:
                message = str(exc)
                for key_name in ("fhl_key", "siliconflow_key"):
                    secret = str(self.api_config.get(key_name) or "")
                    if secret:
                        message = message.replace(secret, "<redacted>")
                self.failed.emit(f"{type(exc).__name__}: {message}")


    class DamageReportReviewDialog(QDialog):
        """Human-readable editor and confirmation gate for a v3 damage report."""

        _LEVEL_OPTIONS = (
            ("待判定", "undetermined"),
            ("低", "low"),
            ("中", "medium"),
            ("高", "high"),
            ("严重", "critical"),
        )

        def __init__(
            self,
            report: Any,
            report_path: str | Path,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            _register_ui_font()
            from runtime.damage_report_schema import DamageReport

            self.report_path = Path(report_path)
            self.current_report = (
                report.model_copy(deep=True)
                if isinstance(report, DamageReport)
                else DamageReport.model_validate(report)
            )
            self.confirmed_report: Any | None = None
            self.finding_editors: list[dict[str, Any]] = []
            self.setWindowTitle("损伤分析报告人工审核")
            self.setObjectName("reportReviewDialog")
            self.setModal(True)
            self.setMinimumSize(820, 560)
            self.resize(1100, 760)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(20, 18, 20, 18)
            layout.setSpacing(12)

            title = QLabel("损伤分析报告人工审核")
            title.setObjectName("panelTitle")
            layout.addWidget(title)

            guidance = QLabel(
                "请核对损伤项、等级、依据与建议。确认前，系统不会生成施工方案。"
            )
            guidance.setWordWrap(True)
            guidance.setObjectName("mutedText")
            layout.addWidget(guidance)

            self.tabs = QTabWidget()
            self.tabs.setObjectName("reportReviewTabs")
            self._build_overall_tab()
            self._build_findings_tab()
            self._build_review_tab()
            self._build_raw_tab()
            self.tabs.currentChanged.connect(self._on_tab_changed)
            layout.addWidget(self.tabs, 1)

            self.status_label = QLabel("报告待审核，可保存草稿后继续编辑。")
            self.status_label.setWordWrap(True)
            self.status_label.setObjectName("mutedText")
            layout.addWidget(self.status_label)

            actions = QHBoxLayout()
            actions.addStretch(1)
            self.save_draft_button = QPushButton("保存审核草稿")
            self.save_draft_button.clicked.connect(self.save_draft)
            actions.addWidget(self.save_draft_button)
            self.confirm_button = QPushButton("确认报告并生成施工方案")
            self.confirm_button.setObjectName("startButton")
            self.confirm_button.clicked.connect(self.confirm_review)
            actions.addWidget(self.confirm_button)
            self.cancel_button = QPushButton("取消")
            self.cancel_button.clicked.connect(self.reject)
            actions.addWidget(self.cancel_button)
            layout.addLayout(actions)

        @staticmethod
        def _optional_text(editor: QLineEdit) -> str | None:
            value = editor.text().strip()
            return value or None

        @staticmethod
        def _multiline_editor(text: str, *, minimum_height: int = 64) -> QPlainTextEdit:
            editor = QPlainTextEdit()
            editor.setPlainText(text)
            editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            editor.setMinimumHeight(minimum_height)
            editor.setTabChangesFocus(True)
            return editor

        @classmethod
        def _level_combo(cls, value: str) -> QComboBox:
            combo = QComboBox()
            for label, canonical in cls._LEVEL_OPTIONS:
                combo.addItem(label, canonical)
            index = combo.findData(value)
            combo.setCurrentIndex(max(0, index))
            combo.setMinimumWidth(140)
            return combo

        @staticmethod
        def _scroll_page(content: QWidget) -> QScrollArea:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            return scroll

        def _build_overall_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            page_layout.setSpacing(10)

            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
            form.setHorizontalSpacing(18)
            form.setVerticalSpacing(10)
            self.subject_edits: dict[str, QLineEdit] = {}
            subject_fields = (
                ("project_name", "项目名称"),
                ("asset_id", "资产/构件编号"),
                ("component", "构件名称"),
                ("inspection_time", "检测时间"),
            )
            for field_name, label in subject_fields:
                editor = QLineEdit()
                editor.setText(getattr(self.current_report.subject, field_name) or "")
                editor.setClearButtonEnabled(True)
                self.subject_edits[field_name] = editor
                form.addRow(label, editor)

            self.project_overview_edit = self._multiline_editor(
                self.current_report.subject.project_overview or "",
                minimum_height=76,
            )
            form.addRow("项目概况", self.project_overview_edit)
            self.executive_summary_edit = self._multiline_editor(
                self.current_report.executive_summary,
                minimum_height=88,
            )
            form.addRow("报告摘要", self.executive_summary_edit)
            self.overall_level_combo = self._level_combo(
                self.current_report.overall_screening_level
            )
            form.addRow("总体辅助等级", self.overall_level_combo)
            self.overall_level_reason_edit = self._multiline_editor(
                self.current_report.overall_level_reason,
                minimum_height=76,
            )
            form.addRow("总体等级理由", self.overall_level_reason_edit)
            page_layout.addLayout(form)
            page_layout.addStretch(1)

            self.overall_tab = self._scroll_page(page)
            self.tabs.addTab(self.overall_tab, "总体信息")

        @staticmethod
        def _reference_display(reference: Any) -> str:
            name = str(reference.name or "").strip()
            reference_id = str(reference.id or "").strip()
            role = str(reference.role or "").strip()
            markers = re.findall(r"\[KB:[^\]\r\n]+\]", role)
            pages: list[str] = []
            for marker in markers:
                page_match = re.search(r":page:(\d+)(?::[^\]]+)?\]$", marker)
                if page_match:
                    page_label = f"第{int(page_match.group(1))}页"
                    if page_label not in pages:
                        pages.append(page_label)
            readable_role = re.sub(r"\[KB:[^\]\r\n]+\]", "", role)
            readable_role = readable_role.strip(" \t\r\n:：,，;；")
            if name and reference_id and name != reference_id:
                source = f"{name}（{reference_id}）"
            else:
                source = name or reference_id or "知识库来源（未核实）"
            details = "；".join(part for part in (readable_role, "、".join(pages)) if part)
            return f"{source}：{details}" if details else source

        def _build_findings_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            page_layout.setSpacing(12)

            for finding in self.current_report.findings:
                group = QGroupBox(
                    f"{finding.image_name}  ·  #{finding.finding_index}  ·  {finding.damage_type}"
                )
                group.setObjectName("reportFindingGroup")
                form = QFormLayout(group)
                form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
                form.setHorizontalSpacing(18)
                form.setVerticalSpacing(9)

                identity = QLabel(f"图片：{finding.image_name}    序号：{finding.finding_index}")
                identity.setWordWrap(True)
                identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
                identity.setObjectName("mutedText")
                form.addRow("证据标识", identity)

                damage_type = QLineEdit(finding.damage_type)
                damage_type.setClearButtonEnabled(True)
                damage_level = self._level_combo(finding.damage_level)
                level_reason = self._multiline_editor(finding.level_reason)
                visual_basis = self._multiline_editor("\n".join(finding.visual_basis))
                uncertainty = self._multiline_editor(finding.uncertainty)
                observed_evidence = self._multiline_editor(finding.observed_evidence)
                risk_interpretation = self._multiline_editor(finding.risk_interpretation)
                recommended_action = self._multiline_editor(finding.recommended_action)
                confidence_note = self._multiline_editor(finding.confidence_note)

                form.addRow("损伤类型", damage_type)
                form.addRow("辅助等级", damage_level)
                form.addRow("等级理由", level_reason)
                if finding.standards_basis:
                    standards_text = "\n".join(
                        f"{index}. {self._reference_display(reference)}"
                        for index, reference in enumerate(finding.standards_basis, start=1)
                    )
                else:
                    standards_text = "未引用规范依据"
                standards = QLabel(standards_text)
                standards.setWordWrap(True)
                standards.setTextInteractionFlags(Qt.TextSelectableByMouse)
                standards.setObjectName("reportStandardsText")
                form.addRow("规范依据（只读）", standards)
                form.addRow("可见依据（一行一项）", visual_basis)
                form.addRow("不确定性", uncertainty)
                form.addRow("观察证据", observed_evidence)
                form.addRow("风险解释", risk_interpretation)
                form.addRow("建议措施", recommended_action)
                form.addRow("置信度说明", confidence_note)
                page_layout.addWidget(group)

                self.finding_editors.append(
                    {
                        "group": group,
                        "identity_label": identity,
                        "standards_label": standards,
                        "damage_type": damage_type,
                        "damage_level": damage_level,
                        "level_reason": level_reason,
                        "visual_basis": visual_basis,
                        "uncertainty": uncertainty,
                        "observed_evidence": observed_evidence,
                        "risk_interpretation": risk_interpretation,
                        "recommended_action": recommended_action,
                        "confidence_note": confidence_note,
                    }
                )
            page_layout.addStretch(1)
            self.findings_tab = self._scroll_page(page)
            self.tabs.addTab(self.findings_tab, f"损伤明细（{len(self.finding_editors)}）")

        def _build_review_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            page_layout.setSpacing(10)
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
            form.setHorizontalSpacing(18)
            form.setVerticalSpacing(10)

            self.reviewer_edit = QLineEdit()
            self.reviewer_edit.setPlaceholderText("确认报告时必填")
            self.reviewer_edit.setText(self.current_report.human_review.reviewer)
            self.reviewer_edit.setClearButtonEnabled(True)
            form.addRow("审核人", self.reviewer_edit)
            self.review_notes_edit = self._multiline_editor(
                self.current_report.human_review.notes,
                minimum_height=94,
            )
            form.addRow("审核备注", self.review_notes_edit)
            self.limitations_edit = self._multiline_editor(
                "\n".join(self.current_report.limitations),
                minimum_height=132,
            )
            self.limitations_edit.setPlaceholderText("每行填写一项局限")
            form.addRow("报告局限（一行一项）", self.limitations_edit)

            integrity = self.current_report.integrity
            integrity_text = (
                f"证据对应：{'有效' if integrity.correspondence_valid else '需检查'}    "
                f"预期 {integrity.expected_count} 项 / 当前 {integrity.actual_count} 项\n"
                f"生成模型：{self.current_report.provenance.model}\n"
                f"生成时间：{self.current_report.provenance.generated_at}"
            )
            if integrity.issues:
                integrity_text += "\n问题：" + "；".join(integrity.issues)
            self.integrity_label = QLabel(integrity_text)
            self.integrity_label.setWordWrap(True)
            self.integrity_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.integrity_label.setObjectName("reportIntegrityText")
            form.addRow("系统校验（只读）", self.integrity_label)
            page_layout.addLayout(form)
            page_layout.addStretch(1)

            self.review_tab = self._scroll_page(page)
            self.tabs.addTab(self.review_tab, "复核与局限")

        def _build_raw_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            page_layout.setSpacing(8)
            note = QLabel("高级诊断数据，仅供查看。请在其他页签中修改报告内容。")
            note.setWordWrap(True)
            note.setObjectName("mutedText")
            page_layout.addWidget(note)
            self.raw_report_editor = QPlainTextEdit()
            self.raw_report_editor.setObjectName("rawReportData")
            self.raw_report_editor.setReadOnly(True)
            self.raw_report_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
            page_layout.addWidget(self.raw_report_editor, 1)
            self.raw_tab = page
            self.raw_tab_index = self.tabs.addTab(page, "原始数据")
            self._refresh_raw_editor(self.current_report)

        def _on_tab_changed(self, index: int) -> None:
            if index == self.raw_tab_index:
                try:
                    self._refresh_raw_editor(self._report_from_form())
                except Exception:
                    self._refresh_raw_editor(self.current_report)

        def _refresh_raw_editor(self, report: Any) -> None:
            self.raw_report_editor.setPlainText(
                json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
            )
            cursor = self.raw_report_editor.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.raw_report_editor.setTextCursor(cursor)

        def _report_from_form(self) -> Any:
            from runtime.damage_report_schema import DamageReport

            edited = self.current_report.model_copy(deep=True)
            for field_name, editor in self.subject_edits.items():
                setattr(edited.subject, field_name, self._optional_text(editor))
            overview = self.project_overview_edit.toPlainText().strip()
            edited.subject.project_overview = overview or None
            edited.executive_summary = self.executive_summary_edit.toPlainText().strip()
            edited.overall_screening_level = str(self.overall_level_combo.currentData())
            edited.overall_level_reason = self.overall_level_reason_edit.toPlainText().strip()

            if len(self.finding_editors) != len(self.current_report.findings):
                raise ValueError("损伤项数量与原始证据不一致")
            for original, finding, controls in zip(
                self.current_report.findings,
                edited.findings,
                self.finding_editors,
            ):
                finding.finding_index = original.finding_index
                finding.image_name = original.image_name
                finding.standards_basis = [
                    reference.model_copy(deep=True) for reference in original.standards_basis
                ]
                finding.damage_type = controls["damage_type"].text().strip()
                finding.damage_level = str(controls["damage_level"].currentData())
                finding.level_reason = controls["level_reason"].toPlainText().strip()
                finding.visual_basis = [
                    line.strip()
                    for line in controls["visual_basis"].toPlainText().splitlines()
                    if line.strip()
                ]
                finding.uncertainty = controls["uncertainty"].toPlainText().strip()
                finding.observed_evidence = controls["observed_evidence"].toPlainText().strip()
                finding.risk_interpretation = controls["risk_interpretation"].toPlainText().strip()
                finding.recommended_action = controls["recommended_action"].toPlainText().strip()
                finding.confidence_note = controls["confidence_note"].toPlainText().strip()

            edited.limitations = [
                line.strip()
                for line in self.limitations_edit.toPlainText().splitlines()
                if line.strip()
            ]
            edited.report_schema_version = self.current_report.report_schema_version
            edited.provenance = self.current_report.provenance.model_copy(deep=True)
            edited.integrity = self.current_report.integrity.model_copy(deep=True)
            edited.review_status = "edited_pending_confirmation"
            edited.human_review.status = "edited_pending_confirmation"
            edited.human_review.reviewed_at = None
            edited.human_review.reviewer = self.reviewer_edit.text().strip()
            edited.human_review.notes = self.review_notes_edit.toPlainText().strip()
            return DamageReport.model_validate(edited.model_dump(mode="json"))

        def _show_error(self, title: str, exc: Exception) -> None:
            message = f"{type(exc).__name__}: {exc}"
            self.status_label.setText(message)
            QMessageBox.warning(self, title, message)

        def save_draft(self) -> None:
            try:
                from runtime.responses_damage_report import save_human_reviewed_report

                self.current_report = save_human_reviewed_report(
                    self.report_path,
                    self._report_from_form(),
                    confirmed=False,
                )
                self._refresh_raw_editor(self.current_report)
                self.status_label.setText("审核草稿已保存，报告仍待确认。")
            except Exception as exc:
                self._show_error("报告草稿未保存", exc)

        def confirm_review(self) -> None:
            try:
                from runtime.responses_damage_report import save_human_reviewed_report

                report = save_human_reviewed_report(
                    self.report_path,
                    self._report_from_form(),
                    confirmed=True,
                )
                self.current_report = report
                self.confirmed_report = report
                self.accept()
            except Exception as exc:
                self._show_error("报告尚未确认", exc)


    class ConstructionPlanReviewDialog(QDialog):
        """Structured engineering review without granting construction release."""

        _WORK_ITEM_TEXT_EDITABLE_FIELDS = (
            ("ai_method_name", "工法名称"),
            ("base_repair_method", "修复工法"),
            ("method_rationale", "工法理由"),
        )
        _WORK_ITEM_EDITABLE_FIELDS = (
            ("applicability_conditions", "适用条件"),
            ("required_site_measurements", "现场复测"),
            ("materials", "材料"),
            ("equipment", "设备"),
            ("procedure_steps", "施工步骤"),
            ("quality_control_points", "质量控制"),
            ("acceptance_checks", "验收检查"),
            ("safety_controls", "安全措施"),
            ("method_required_site_verification", "工法现场核验"),
            ("method_upgrade_conditions", "升级评估条件"),
            ("method_code_references", "工法规范引用"),
            ("stop_work_conditions", "停工条件"),
            ("method_excluded_conclusions", "不得推定事项"),
            ("assumptions", "假设与限制"),
        )
        _GENERAL_EDITABLE_FIELDS = (
            ("preconstruction_checks", "施工前检查"),
            ("general_quality_requirements", "总体质量要求"),
            ("general_safety_requirements", "总体安全要求"),
            ("post_repair_inspection", "修复后复检"),
            ("schedule_assumptions", "工期假设"),
            ("excluded_items", "不包含事项"),
            ("limitations", "方案局限"),
        )

        def __init__(
            self,
            plan: Any,
            plan_path: str | Path,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            _register_ui_font()
            from runtime.construction_plan_schema import ConstructionPlan

            self.plan_path = Path(plan_path)
            self.current_plan = (
                plan.model_copy(deep=True)
                if isinstance(plan, ConstructionPlan)
                else ConstructionPlan.model_validate(plan)
            )
            self.confirmed_plan: Any | None = None
            self.work_item_editors: list[dict[str, QPlainTextEdit]] = []
            self.general_editors: dict[str, QPlainTextEdit] = {}
            self.setWindowTitle("施工方案人工审核")
            self.setObjectName("constructionPlanReviewDialog")
            self.setModal(True)
            self.setMinimumSize(840, 580)
            self.resize(1120, 780)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(20, 18, 20, 18)
            layout.setSpacing(12)
            title = QLabel("施工方案人工审核")
            title.setObjectName("panelTitle")
            layout.addWidget(title)
            self.guidance_label = QLabel(
                "请核对施工范围、分项工序、质量与安全要求。审核确认仅表示已完成工程师复核，"
                "不代表施工放行；正式批准仍须在系统外完成。"
            )
            self.guidance_label.setWordWrap(True)
            self.guidance_label.setObjectName("mutedText")
            layout.addWidget(self.guidance_label)

            self.tabs = QTabWidget()
            self.tabs.setObjectName("constructionPlanReviewTabs")
            self._build_overall_tab()
            self._build_work_items_tab()
            self._build_general_tab()
            self._build_review_tab()
            self._build_raw_tab()
            self.tabs.currentChanged.connect(self._on_tab_changed)
            layout.addWidget(self.tabs, 1)

            self.status_label = QLabel("施工方案待审核，可先保存草稿。")
            self.status_label.setWordWrap(True)
            self.status_label.setObjectName("mutedText")
            layout.addWidget(self.status_label)

            actions = QHBoxLayout()
            actions.addStretch(1)
            self.save_draft_button = QPushButton("保存审核草稿")
            self.save_draft_button.clicked.connect(self.save_draft)
            actions.addWidget(self.save_draft_button)
            self.confirm_button = QPushButton("确认审核（不代表施工放行）")
            self.confirm_button.setObjectName("startButton")
            self.confirm_button.clicked.connect(self.confirm_review)
            actions.addWidget(self.confirm_button)
            self.cancel_button = QPushButton("关闭")
            self.cancel_button.clicked.connect(self.reject)
            actions.addWidget(self.cancel_button)
            layout.addLayout(actions)

        @staticmethod
        def _multiline_editor(values: str | list[str], *, minimum_height: int = 70) -> QPlainTextEdit:
            editor = QPlainTextEdit()
            text = values if isinstance(values, str) else "\n".join(values)
            editor.setPlainText(text)
            editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            editor.setMinimumHeight(minimum_height)
            editor.setTabChangesFocus(True)
            return editor

        @staticmethod
        def _lines(editor: QPlainTextEdit) -> list[str]:
            return [line.strip() for line in editor.toPlainText().splitlines() if line.strip()]

        @staticmethod
        def _scroll_page(content: QWidget) -> QScrollArea:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            return scroll

        @staticmethod
        def _readonly_label(text: str) -> QLabel:
            label = QLabel(text or "—")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setObjectName("mutedText")
            return label

        def _build_overall_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
            form.setHorizontalSpacing(18)
            form.setVerticalSpacing(10)
            form.addRow("项目概况（只读）", self._readonly_label(self.current_plan.project_overview or "未填写"))
            self.scope_edit = self._multiline_editor(self.current_plan.scope, minimum_height=82)
            self.executive_summary_edit = self._multiline_editor(
                self.current_plan.executive_summary, minimum_height=96
            )
            form.addRow("施工范围", self.scope_edit)
            form.addRow("方案摘要", self.executive_summary_edit)
            evidence_text = (
                f"方案状态：{self.current_plan.plan_status}\n"
                f"证据一致性：{self.current_plan.evidence_consistency_status}  "
                f"({self.current_plan.actual_evidence_count}/{self.current_plan.expected_evidence_count})\n"
                f"{self.current_plan.evidence_consistency_note}\n"
                "施工放行：否"
            )
            form.addRow("系统状态（只读）", self._readonly_label(evidence_text))
            page_layout.addLayout(form)
            page_layout.addStretch(1)
            self.overall_tab = self._scroll_page(page)
            self.tabs.addTab(self.overall_tab, "总体信息")

        def _build_work_items_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            page_layout.setSpacing(12)
            for item in self.current_plan.work_items:
                displayed_method_name = item.ai_method_name or item.method_display_name
                group = QGroupBox(
                    f"{item.image_name}  ·  #{item.finding_index}  ·  {displayed_method_name}"
                )
                form = QFormLayout(group)
                form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
                form.setHorizontalSpacing(18)
                form.setVerticalSpacing(9)
                identity = (
                    f"分项：{item.work_item_id}    损伤：{item.finding_id}\n"
                    f"类型/等级：{item.damage_type} / {item.damage_level}\n"
                    f"AI 方案决策状态：{item.decision_status}\n"
                    f"工法来源：{item.repair_method_source}\n"
                    f"工程量依据：{item.quantity_basis}\n"
                    f"构件面积占比：{item.component_area_ratio if item.component_area_ratio is not None else '无可靠尺度'}    "
                    f"实体面积：{item.physical_area_mm2 if item.physical_area_mm2 is not None else '无可靠尺度'}"
                )
                form.addRow("身份与安全边界（只读）", self._readonly_label(identity))
                report_basis = (
                    f"等级理由：{item.report_damage_level_reason}\n"
                    f"观察证据：{item.report_observed_evidence}\n"
                    f"风险解释：{item.report_risk_interpretation}\n"
                    f"报告建议：{item.report_recommended_action}\n"
                    f"不确定性：{item.report_uncertainty}"
                )
                form.addRow("报告依据（只读）", self._readonly_label(report_basis))
                controls: dict[str, QPlainTextEdit] = {}
                legacy_text_defaults = {
                    "ai_method_name": displayed_method_name,
                    "base_repair_method": item.base_repair_method,
                    "method_rationale": item.method_rationale or "请工程师结合现场复核确认该工法。",
                }
                for field_name, label in self._WORK_ITEM_TEXT_EDITABLE_FIELDS:
                    editor = self._multiline_editor(
                        legacy_text_defaults[field_name],
                        minimum_height=82 if field_name != "ai_method_name" else 58,
                    )
                    editor.setPlaceholderText("请审核并完善 AI 起草内容")
                    controls[field_name] = editor
                    form.addRow(label, editor)
                for field_name, label in self._WORK_ITEM_EDITABLE_FIELDS:
                    editor = self._multiline_editor(getattr(item, field_name))
                    editor.setPlaceholderText("每行填写一项")
                    controls[field_name] = editor
                    form.addRow(f"{label}（一行一项）", editor)
                page_layout.addWidget(group)
                self.work_item_editors.append(controls)
            page_layout.addStretch(1)
            self.work_items_tab = self._scroll_page(page)
            self.tabs.addTab(self.work_items_tab, f"施工分项（{len(self.work_item_editors)}）")

        def _build_general_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
            form.setHorizontalSpacing(18)
            form.setVerticalSpacing(10)
            for field_name, label in self._GENERAL_EDITABLE_FIELDS:
                editor = self._multiline_editor(getattr(self.current_plan, field_name), minimum_height=76)
                editor.setPlaceholderText("每行填写一项")
                self.general_editors[field_name] = editor
                form.addRow(f"{label}（一行一项）", editor)
            page_layout.addLayout(form)
            page_layout.addStretch(1)
            self.general_tab = self._scroll_page(page)
            self.tabs.addTab(self.general_tab, "通用要求")

        def _build_review_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
            form.setHorizontalSpacing(18)
            form.setVerticalSpacing(10)
            self.reviewer_edit = QLineEdit(self.current_plan.human_review.reviewer)
            self.reviewer_edit.setPlaceholderText("确认施工方案审核时必填")
            self.reviewer_edit.setClearButtonEnabled(True)
            self.review_notes_edit = self._multiline_editor(
                self.current_plan.human_review.notes, minimum_height=112
            )
            form.addRow("审核人", self.reviewer_edit)
            form.addRow("审核备注", self.review_notes_edit)
            form.addRow(
                "确认边界（只读）",
                self._readonly_label(
                    "确认后仍保持 construction_released=false。若方案状态为 hold 或 "
                    "evidence_inconsistent，审核不能解除阻断，也不能生成修复渲染。"
                ),
            )
            page_layout.addLayout(form)
            page_layout.addStretch(1)
            self.review_tab = self._scroll_page(page)
            self.tabs.addTab(self.review_tab, "审核确认")

        def _build_raw_tab(self) -> None:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            note = QLabel("高级诊断数据，仅供查看。请在其他页签中修改允许审核的施工说明。")
            note.setWordWrap(True)
            note.setObjectName("mutedText")
            page_layout.addWidget(note)
            self.raw_plan_editor = QPlainTextEdit()
            self.raw_plan_editor.setReadOnly(True)
            self.raw_plan_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
            page_layout.addWidget(self.raw_plan_editor, 1)
            self.raw_tab_index = self.tabs.addTab(page, "原始数据")
            self._refresh_raw_editor(self.current_plan)

        def _on_tab_changed(self, index: int) -> None:
            if index == self.raw_tab_index:
                try:
                    self._refresh_raw_editor(self._plan_from_form())
                except Exception:
                    self._refresh_raw_editor(self.current_plan)

        def _refresh_raw_editor(self, plan: Any) -> None:
            self.raw_plan_editor.setPlainText(
                json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
            )
            cursor = self.raw_plan_editor.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.raw_plan_editor.setTextCursor(cursor)

        def _plan_from_form(self) -> Any:
            from runtime.construction_plan_schema import ConstructionPlan

            if len(self.work_item_editors) != len(self.current_plan.work_items):
                raise ValueError("施工分项数量与原方案不一致")
            edited = self.current_plan.model_copy(deep=True)
            edited.scope = self.scope_edit.toPlainText().strip()
            edited.executive_summary = self.executive_summary_edit.toPlainText().strip()
            for original, item, controls in zip(
                self.current_plan.work_items,
                edited.work_items,
                self.work_item_editors,
            ):
                for field_name, _label in self._WORK_ITEM_TEXT_EDITABLE_FIELDS:
                    setattr(item, field_name, controls[field_name].toPlainText().strip())
                for field_name, _label in self._WORK_ITEM_EDITABLE_FIELDS:
                    setattr(item, field_name, self._lines(controls[field_name]))
                item.work_item_id = original.work_item_id
                item.repair_item_id = original.repair_item_id
                item.finding_id = original.finding_id
            for field_name, _label in self._GENERAL_EDITABLE_FIELDS:
                setattr(edited, field_name, self._lines(self.general_editors[field_name]))
            edited.plan_schema_version = self.current_plan.plan_schema_version
            edited.plan_status = self.current_plan.plan_status
            edited.construction_released = False
            edited.provenance = self.current_plan.provenance.model_copy(deep=True)
            edited.review_status = "edited_pending_confirmation"
            edited.human_review.status = "edited_pending_confirmation"
            edited.human_review.reviewer = self.reviewer_edit.text().strip()
            edited.human_review.reviewed_at = None
            edited.human_review.notes = self.review_notes_edit.toPlainText().strip()
            return ConstructionPlan.model_validate(edited.model_dump(mode="json"))

        def _show_error(self, title: str, exc: Exception) -> None:
            message = f"{type(exc).__name__}: {exc}"
            self.status_label.setText(message)
            QMessageBox.warning(self, title, message)

        def save_draft(self) -> None:
            try:
                from runtime.responses_construction_plan import save_human_reviewed_construction_plan

                self.current_plan = save_human_reviewed_construction_plan(
                    self.plan_path, self._plan_from_form(), confirmed=False
                )
                self._refresh_raw_editor(self.current_plan)
                self.status_label.setText("审核草稿已保存，施工方案仍待确认。")
            except Exception as exc:
                self._show_error("施工方案草稿未保存", exc)

        def confirm_review(self) -> None:
            try:
                from runtime.responses_construction_plan import save_human_reviewed_construction_plan

                plan = save_human_reviewed_construction_plan(
                    self.plan_path, self._plan_from_form(), confirmed=True
                )
                self.current_plan = plan
                self.confirmed_plan = plan
                self.accept()
            except Exception as exc:
                self._show_error("施工方案尚未确认", exc)


    class DamageWorkflowWindow(QMainWindow):
        generation_update = Signal(str, str)

        def __init__(self) -> None:
            super().__init__()
            _register_ui_font()
            self.setWindowTitle("構件視界｜損傷修復工程流程")
            self._set_initial_window_size()
            self.folder: Path | None = None
            self.selected_image_paths: list[Path] | None = None
            self.output_dir: Path | None = None
            self.summary: dict[str, Any] | None = None
            self.live_results: dict[str, dict[str, Any]] = {}
            self.settings_store = SettingsStore(self._settings_path(), self._api_config_path())
            self.settings: AppSettings = self.settings_store.load()
            self.api_config = self.settings.api.to_legacy()
            self.worker: RecognitionWorker | None = None
            self.auto_worker: AutomaticWorkflowWorker | None = None
            self.plan_worker: ConstructionPlanWorker | None = None
            self.render_worker: RepairRenderWorker | None = None
            self.workflow_stop_requested = False
            self.workflow_retry_pending = False
            self.current_report: Any | None = None
            self.current_construction_plan: Any | None = None
            self.plan_summary: dict[str, Any] | None = None
            self.render_results: list[Any] = []
            self.generated_report_docx: Path | None = None
            self.generated_report_markdown: Path | None = None
            self.generated_construction_plan_markdown: Path | None = None
            self._generation_kind = ""
            self._generation_text = ""
            self._generation_active = False
            self._review_dialog_open = False
            self._review_dialog_scheduled = False
            self._construction_review_dialog_open = False
            self._construction_review_dialog_scheduled = False
            self._plan_generation_scheduled = False
            self._layout_scale: float | None = None
            self._layout_signature: tuple[Any, ...] | None = None
            self._layout_update_pending = False
            self._responsive_layout_specs: list[tuple[Any, tuple[int, int, int, int], int]] = []
            self.stage_index = 0
            self.stage_buttons: list[QPushButton] = []
            self._build_ui()
            self.generation_update.connect(self._on_generation_update)
            self._apply_theme()

        def _set_initial_window_size(self) -> None:
            """Fit the initial window to the usable screen while keeping a desktop minimum."""
            preferred_width, preferred_height = 1680, 980
            screen = QApplication.primaryScreen()
            if screen is None:
                self.setMinimumSize(900, 600)
                self.resize(preferred_width, preferred_height)
                return
            available = screen.availableGeometry()
            # Keep the taskbar and display work-area margins clear.  Do not force
            # the minimum beyond what an unusually small test/display can hold.
            minimum_width = min(1024, available.width())
            minimum_height = min(640, available.height())
            self.setMinimumSize(minimum_width, minimum_height)
            width = min(preferred_width, max(minimum_width, int(available.width() * 0.96)))
            height = min(preferred_height, max(minimum_height, int(available.height() * 0.96)))
            self.resize(width, height)

        @staticmethod
        def _api_config_path() -> Path:
            return user_config_path("api_config.json")

        @staticmethod
        def _settings_path() -> Path:
            return user_config_path("gui_settings.json")

        def _load_api_config(self) -> dict[str, str]:
            store = getattr(self, "settings_store", None)
            if store is None:
                store = SettingsStore(self._settings_path(), self._api_config_path())
            self.settings = store.load()
            return self.settings.api.to_legacy()

        def _save_api_config(self, values: dict[str, str]) -> None:
            self.api_config.update(values)
            for field_name in (
                "responses_url",
                "responses_key",
                "responses_model",
                "fhl_url",
                "fhl_key",
                "repair_render_provider",
                "siliconflow_url",
                "siliconflow_key",
                "siliconflow_model",
            ):
                if field_name in self.api_config:
                    setattr(self.settings.api, field_name, self.api_config[field_name])
            self.settings_store.save(self.settings)

        def _set_generated_report_docx(self, path: Path) -> None:
            self.generated_report_docx = path
            if hasattr(self, "report_document_path_label"):
                self.report_document_path_label.setText(f"当前报告：{path.name}")
            if hasattr(self, "open_report_word_button"):
                self.open_report_word_button.setEnabled(True)

        def _set_report_docx_failure(self, reason: str) -> None:
            if hasattr(self, "report_document_path_label"):
                self.report_document_path_label.setText(f"Word 报告未生成：{reason}")
            if hasattr(self, "open_report_word_button"):
                self.open_report_word_button.setEnabled(False)

        def _set_generated_markdown_paths(self) -> None:
            if self.output_dir is None:
                return
            report_path = self.output_dir / "report.md"
            construction_path = self.output_dir / "construction_plan.md"
            self.generated_report_markdown = report_path if report_path.is_file() else None
            self.generated_construction_plan_markdown = construction_path if construction_path.is_file() else None
            if hasattr(self, "report_document_path_label"):
                if self.generated_report_markdown and self.generated_construction_plan_markdown:
                    self.report_document_path_label.setText(
                        f"分析报告：{report_path.name}  ｜  施工方案：{construction_path.name}"
                    )
                elif self.generated_report_markdown:
                    self.report_document_path_label.setText(f"分析报告：{report_path.name}")
                else:
                    self.report_document_path_label.setText("尚未生成分析报告")
            if hasattr(self, "open_report_word_button"):
                self.open_report_word_button.setEnabled(self.generated_report_markdown is not None)
            if hasattr(self, "open_construction_plan_button"):
                self.open_construction_plan_button.setEnabled(
                    self.generated_construction_plan_markdown is not None
                )
            summary_path = self.output_dir / "batch_summary.json"
            if summary_path.is_file():
                try:
                    from runtime.assistant.context import record_detection_snapshot

                    record_detection_snapshot(summary_path, include_artifacts=True)
                except Exception:
                    pass

        def _open_generated_markdown(self, path: Path | None, *, title: str) -> None:
            if path is None or not path.is_file():
                QMessageBox.information(self, f"暂无{title}", f"请先生成{title}。")
                return
            try:
                if os.name == "nt":
                    os.startfile(str(path))
                else:  # pragma: no cover - desktop deployment branch
                    import subprocess

                    subprocess.Popen(["xdg-open", str(path)])
            except OSError as exc:
                QMessageBox.warning(self, f"无法打开{title}", str(exc))

        def open_generated_report(self) -> None:
            path = self.generated_report_markdown
            if path is None and self.output_dir is not None:
                candidate = self.output_dir / "report.md"
                path = candidate if candidate.is_file() else None
            self._open_generated_markdown(path, title="分析报告")

        def open_generated_construction_plan(self) -> None:
            path = self.generated_construction_plan_markdown
            if path is None and self.output_dir is not None:
                candidate = self.output_dir / "construction_plan.md"
                path = candidate if candidate.is_file() else None
            self._open_generated_markdown(path, title="施工方案")

        def _record_template_failure(
            self,
            profile_name: str,
            *,
            template_id: str,
            reason: str,
        ) -> None:
            if self.output_dir is None:
                return
            manifest_path = self.output_dir / "generation_manifest.json"
            payload: dict[str, Any] = {}
            if manifest_path.is_file():
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = {}
            payload.setdefault("templates", {})[profile_name] = {
                "template": template_id,
                "status": "failed",
                "error": reason,
            }
            manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        def open_generated_report_in_word(self) -> None:
            path = self.generated_report_docx
            if path is None and self.output_dir is not None:
                candidate = self.output_dir / "損傷分析報告.docx"
                path = candidate if candidate.is_file() else None
            if path is None or not path.is_file():
                QMessageBox.information(self, "暂无 Word 报告", "请先生成带 Word 模板的损伤分析报告。")
                return
            try:
                if os.name == "nt":
                    os.startfile(str(path))
                else:  # pragma: no cover - desktop deployment branch
                    import subprocess

                    subprocess.Popen(["xdg-open", str(path)])
            except OSError as exc:
                QMessageBox.warning(self, "无法打开 Word", str(exc))

        def _render_profile_docx(self, profile_name: str, values: dict[str, Any]) -> Path | None:
            """Render a selected DOCX template while preserving JSON/Markdown fallbacks."""
            if self.output_dir is None:
                return None
            profile = self.settings.generation_profiles.get(profile_name)
            if profile is None or not profile.template_id:
                if profile_name == "損傷分析報告":
                    self._set_report_docx_failure("未选择 Word 模板")
                return None
            metadata = self.settings.templates.get(profile.template_id)
            if not metadata or metadata.get("status") != "ready":
                reason = "模板未就绪，请在设置中重新上传 Word 模板"
                self._log_event(f"{profile_name}：{reason}，已保留非 DOCX 输出")
                self._record_template_failure(profile_name, template_id=profile.template_id, reason=reason)
                if profile_name == "損傷分析報告":
                    self._set_report_docx_failure(reason)
                return None
            try:
                from runtime.document_templates import (
                    IMAGE_SLOT_NAMES,
                    render_docx_with_status,
                    scan_template,
                )

                values = dict(values)
                if profile_name == "損傷分析報告":
                    from runtime.concrete_damage_report_mapping import build_concrete_damage_template_values

                    values = build_concrete_damage_template_values(self.summary, values)
                else:
                    subject = values.get("subject")
                    if isinstance(subject, dict):
                        for key, value in subject.items():
                            if value is not None:
                                values.setdefault(key, value)
                    values = {key: "" if value is None else value for key, value in values.items()}
                template_metadata = scan_template(metadata["path"])
                for field in template_metadata.placeholders:
                    if not field.startswith(("#", "/")) and field not in IMAGE_SLOT_NAMES:
                        values.setdefault(field, "未提供" if profile_name == "損傷分析報告" else "")

                selected_slots = _report_image_slots_from_summary(self.summary) if profile_name == "損傷分析報告" else {}
                image_slots = {
                    field: selected_slots.get(field)
                    for field in template_metadata.placeholders
                    if field in IMAGE_SLOT_NAMES
                }

                output = self.output_dir / f"{profile_name}.docx"
                output_text_cleanup = (
                    {
                        "（示例）": "",
                        "（无物理标定）mm²": "（无物理标定）",
                        "（无物理标定）mm": "（无物理标定）",
                        "%%": "%",
                    }
                    if profile_name == "損傷分析報告"
                    else None
                )
                render_result = render_docx_with_status(
                    metadata["path"],
                    output,
                    values,
                    image_slots=image_slots,
                    optional_image_slots=(
                        {"image_measurement"}
                        if profile_name == "損傷分析報告" and "image_measurement" in image_slots
                        else set()
                    ),
                    output_text_cleanup=output_text_cleanup,
                    required=profile.template_required,
                )
                if profile_name == "損傷分析報告":
                    self._set_generated_report_docx(render_result.path)
                self._log_event(f"{profile_name}：DOCX 已生成：{output.name}")
                manifest_path = self.output_dir / "generation_manifest.json"
                payload: dict[str, Any] = {}
                if manifest_path.is_file():
                    try:
                        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        payload = {}
                slot_payload = {
                    item.slot: {
                        "status": item.status,
                        "source_path": item.source_path,
                        "error": item.error,
                    }
                    for item in render_result.image_slots
                }
                payload.setdefault("templates", {})[profile_name] = {
                    "template": f"{profile.template_id}:v{metadata.get('version', 1)}",
                    "output_path": str(render_result.path),
                    "status": "ready" if not render_result.unreplaced_placeholders else "failed",
                    "image_slots": slot_payload,
                }
                manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                if slot_payload:
                    states = "，".join(f"{slot}:{item['status']}" for slot, item in slot_payload.items())
                    self._log_event(f"{profile_name}：图像槽位 {states}")
                return render_result.path
            except Exception as exc:
                reason = (
                    "模板文件不存在，请在设置中重新上传 Word 模板"
                    if isinstance(exc, FileNotFoundError)
                    else f"DOCX 渲染失败（{type(exc).__name__}）：{exc}"
                )
                self._log_event(f"{profile_name}：{reason}，已保留非 DOCX 输出")
                self._record_template_failure(profile_name, template_id=profile.template_id, reason=reason)
                if profile_name == "損傷分析報告":
                    self._set_report_docx_failure(reason)
                return None

        def _record_generation_manifest(self, profile_name: str, evidence: dict[str, Any]) -> None:
            if self.output_dir is None:
                return
            snapshot_id, snapshot = self.settings.snapshot()
            profile = snapshot.generation_profiles[profile_name]
            context = build_generation_context(profile, evidence, settings_snapshot_id=snapshot_id)
            manifest_path = self.output_dir / "generation_manifest.json"
            payload: dict[str, Any] = {}
            if manifest_path.is_file():
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = {}
            payload.setdefault("profiles", {})[profile_name] = context.manifest(model=profile.model or snapshot.api.responses_model)
            manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        def _write_profile_reference_markdown(self, profile_name: str, evidence: dict[str, Any]) -> None:
            if self.output_dir is None:
                return
            snapshot_id, snapshot = self.settings.snapshot()
            profile = snapshot.generation_profiles[profile_name]
            kb_root = resolve_user_path(snapshot.knowledge_base.root_dir, default=user_knowledge_base_root())
            kb_result = _retrieve_profile_knowledge(kb_root, profile, snapshot.knowledge_base, evidence)
            chunks = list(kb_result.chunks)
            context = build_generation_context(
                profile,
                evidence,
                settings_snapshot_id=snapshot_id,
                retrieved_chunks=chunks,
                retrieval_mode=kb_result.retrieval_mode,
                knowledge_base_scope_document_ids=kb_result.scope_document_ids,
                anchors=kb_result.anchors,
                context_groups=kb_result.context_groups,
                retrieval_warnings=kb_result.warnings,
                route_diagnostics=kb_result.route_diagnostics,
                retrieval_query=build_profile_query(profile.name, evidence),
                web_search_provider=_generation_web_search_provider(self.api_config, profile.model),
            )
            filename = "construction_plan.md"
            (self.output_dir / filename).write_text(
                render_reference_appendix(context, title=profile_name),
                encoding="utf-8",
            )

        def _open_settings_vertical(self) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("设置")
            dialog.resize(1180, 800)
            dialog.setMinimumSize(980, 680)

            root = QVBoxLayout(dialog)
            root.setContentsMargins(14, 14, 14, 14)
            root.setSpacing(12)
            content_row = QHBoxLayout()
            content_row.setSpacing(14)
            nav = QListWidget()
            nav.setObjectName("settingsNav")
            nav.setFixedWidth(220)
            nav.addItems(["API", "知识库", "损伤分析报告", "施工方案", "修复渲染图"])
            content_row.addWidget(nav)
            stack = QStackedWidget()
            content_row.addWidget(stack, 1)
            root.addLayout(content_row, 1)

            pages: dict[str, QWidget] = {}
            profile_edits: dict[str, QPlainTextEdit] = {}
            profile_models: dict[str, QLineEdit] = {}
            profile_folder_trees: dict[str, QTreeWidget] = {}

            def add_page(key: str, title: str) -> tuple[QWidget, QVBoxLayout]:
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.setContentsMargins(28, 24, 28, 24)
                layout.setSpacing(14)
                heading = QLabel(title)
                heading.setObjectName("settingsPageTitle")
                layout.addWidget(heading)
                pages[key] = page
                stack.addWidget(page)
                return page, layout

            api_page, api_layout = add_page("API", "API 设置")
            api_form = QFormLayout()
            api_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            responses_url = QLineEdit(self.api_config["responses_url"])
            responses_key = QLineEdit(self.api_config["responses_key"]); responses_key.setEchoMode(QLineEdit.Password)
            responses_model = QLineEdit(self.api_config["responses_model"])
            render_provider = QComboBox()
            render_provider.addItem("FHL", "fhl")
            render_provider.addItem("硅基流动", "siliconflow")
            provider_index = render_provider.findData(self.api_config.get("repair_render_provider", "fhl"))
            render_provider.setCurrentIndex(max(0, provider_index))
            fhl_url = QLineEdit(self.api_config["fhl_url"])
            fhl_key = QLineEdit(self.api_config["fhl_key"]); fhl_key.setEchoMode(QLineEdit.Password)
            siliconflow_url = QLineEdit(self.api_config["siliconflow_url"])
            siliconflow_key = QLineEdit(self.api_config["siliconflow_key"]); siliconflow_key.setEchoMode(QLineEdit.Password)
            siliconflow_model = QLineEdit(self.api_config["siliconflow_model"])
            api_form.addRow("Responses API URL", responses_url)
            api_form.addRow("Responses API Key", responses_key)
            api_form.addRow("Responses 模型", responses_model)
            api_form.addRow("修复渲染平台", render_provider)
            api_form.addRow("FHL Image Gen API URL", fhl_url)
            api_form.addRow("FHL Image Gen API Key", fhl_key)
            api_form.addRow("硅基流动 API URL", siliconflow_url)
            api_form.addRow("硅基流动 API Key", siliconflow_key)
            api_form.addRow("硅基流动图像模型", siliconflow_model)
            api_layout.addLayout(api_form)
            api_note = QLabel("密钥只保存在本机配置文件中，界面始终遮罩显示；Responses、FHL 生图与硅基流动分别使用各自独立的 URL 和 Key，不会互相替代。")
            api_note.setObjectName("mutedText"); api_note.setWordWrap(True); api_layout.addWidget(api_note)
            api_layout.addStretch(1)

            kb_page, kb_layout = add_page("知识库", "知识库")
            kb_layout.addWidget(QLabel("像资源管理器一样管理本地资料。支持多级文件夹，双击文件可打开原始文档。"))
            kb_root = resolve_user_path(self.settings.knowledge_base.root_dir, default=user_knowledge_base_root())
            kb = KnowledgeBase(kb_root)
            catalog_migration = bootstrap_active_catalog(self.settings, kb)
            if catalog_migration["imported_document_ids"]:
                self.settings_store.save(self.settings)
            kb_splitter = QSplitter(Qt.Horizontal)
            kb_tree = QTreeWidget(); kb_tree.setHeaderLabels(["知识库目录"]); kb_tree.setContextMenuPolicy(Qt.CustomContextMenu)
            kb_tree.setMinimumWidth(250)
            kb_splitter.addWidget(kb_tree)

            file_panel = QWidget()
            file_layout = QVBoxLayout(file_panel)
            file_layout.setContentsMargins(0, 0, 0, 0)
            file_layout.setSpacing(10)
            kb_breadcrumb = QLabel("当前位置：全部知识库")
            kb_breadcrumb.setObjectName("kbBreadcrumb")
            file_layout.addWidget(kb_breadcrumb)
            kb_toolbar = QHBoxLayout()
            new_folder = QPushButton("＋ 新建文件夹")
            add_kb = QPushButton("＋ 添加文件")
            rename_folder = QPushButton("重命名")
            delete_folder = QPushButton("删除文件夹")
            remove_kb = QPushButton("删除文件")
            for button in (new_folder, add_kb, rename_folder, delete_folder, remove_kb):
                button.setObjectName("compactButton"); kb_toolbar.addWidget(button)
            new_folder.setIcon(QApplication.style().standardIcon(QStyle.SP_FileDialogNewFolder))
            add_kb.setIcon(QApplication.style().standardIcon(QStyle.SP_FileIcon))
            rename_folder.setIcon(QApplication.style().standardIcon(QStyle.SP_FileDialogContentsView))
            delete_folder.setIcon(QApplication.style().standardIcon(QStyle.SP_TrashIcon))
            remove_kb.setIcon(QApplication.style().standardIcon(QStyle.SP_TrashIcon))
            kb_status = QLabel("知识库导入就绪")
            kb_status.setObjectName("mutedText")
            kb_status.setWordWrap(True)
            cancel_kb = QPushButton("停止导入")
            cancel_kb.setObjectName("compactButton")
            cancel_kb.setEnabled(False)
            for button in (new_folder, add_kb, rename_folder, delete_folder, remove_kb, cancel_kb):
                button.setMinimumWidth(button.sizeHint().width())
            kb_toolbar.addWidget(cancel_kb)
            kb_toolbar.addStretch(1)
            file_layout.addLayout(kb_toolbar)
            file_layout.addWidget(kb_status)
            active_snapshot = active_rag_snapshot()
            semantic_status = active_snapshot.get("semantic") or {}
            semantic_manifest = semantic_status.get("manifest") or {}
            kb_active_status = QLabel(
                "生产 active RAG："
                f"{len(active_snapshot['document_ids'])} 个有效文档；"
                f"语义检索 {'已启用' if semantic_status.get('healthy') and semantic_status.get('configured') else '未启用'}"
                f"（{semantic_manifest.get('model_name') or 'unknown'}，{semantic_status.get('retrieval_count', 0)} 条）；"
                "重排 rerank.v1 已启用；"
                f"存储范围 {active_snapshot['storage_scope'] or 'unknown'}；"
                f"manifest {active_snapshot['manifest_path'] or '不可用'}"
            )
            kb_active_status.setObjectName("mutedText")
            kb_active_status.setWordWrap(True)
            file_layout.addWidget(kb_active_status)

            def settings_dialog_open() -> bool:
                """Return whether the settings widgets are still safe to update."""
                try:
                    return bool(dialog.isVisible())
                except RuntimeError:
                    # Qt has already destroyed the dialog after the user saved.
                    return False

            def set_kb_status(message: str) -> None:
                if settings_dialog_open():
                    kb_status.setText(message)

            kb_list = QTreeWidget()
            kb_list.setObjectName("knowledgeFileList")
            kb_list.setHeaderLabels(["名称", "类型", "大小", "状态", "来源位置"])
            kb_list.setRootIsDecorated(False)
            kb_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
            kb_list.setContextMenuPolicy(Qt.CustomContextMenu)
            kb_list.header().setSectionResizeMode(0, QHeaderView.Stretch)
            for column in (1, 2, 3):
                kb_list.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
            kb_list.header().setSectionResizeMode(4, QHeaderView.Stretch)
            file_layout.addWidget(kb_list, 1)
            kb_splitter.addWidget(file_panel)
            kb_splitter.setSizes([280, 680])
            kb_layout.addWidget(kb_splitter, 1)

            def selected_folder_id() -> str:
                item = kb_tree.currentItem()
                return str(item.data(0, Qt.UserRole) or "") if item else ""

            def refresh_folder_tree() -> None:
                kb_tree.clear()
                root_item = QTreeWidgetItem(["知识库（全部内容）"]); root_item.setData(0, Qt.UserRole, ""); kb_tree.addTopLevelItem(root_item)
                items: dict[str, QTreeWidgetItem] = {"": root_item}
                for folder in kb.list_folders():
                    item = QTreeWidgetItem([folder.name]); item.setData(0, Qt.UserRole, folder.folder_id)
                    items.get(folder.parent_id, root_item).addChild(item); items[folder.folder_id] = item
                kb_tree.expandAll(); kb_tree.setCurrentItem(root_item)

            def refresh_kb_list() -> None:
                kb_list.clear()
                current_active_ids = active_document_ids()
                current = kb_tree.currentItem()
                kb_breadcrumb.setText(f"当前位置：{current.text(0) if current else '全部知识库'}")
                records = kb.list_documents_in_folders([selected_folder_id()]) if selected_folder_id() else kb.list_documents()
                for record in records:
                    size = record.size_bytes
                    size_text = f"{size / 1024 / 1024:.2f} MB" if size >= 1024 * 1024 else f"{max(1, size // 1024)} KB"
                    status_text = {"ready": "已索引", "failed": "失败", "pending": "处理中"}.get(record.status, record.status)
                    if record.status == "ready":
                        status_text = "生产已激活" if record.document_id in current_active_ids else "已索引，待生产同步"
                    item = QTreeWidgetItem([record.name, record.extension.lstrip(".").upper(), size_text, status_text, record.path])
                    item.setData(0, Qt.UserRole, record.document_id); item.setData(0, Qt.UserRole + 1, record.path); kb_list.addTopLevelItem(item)

            def create_folder() -> None:
                name, accepted = QInputDialog.getText(dialog, "新建文件夹", "文件夹名称")
                if accepted and name.strip():
                    try: kb.create_folder(name, parent_id=selected_folder_id()); refresh_folder_tree(); refresh_kb_list(); refresh_profile_trees()
                    except Exception as exc: QMessageBox.warning(dialog, "新建失败", str(exc))

            def rename_current_folder() -> None:
                folder_id = selected_folder_id()
                if not folder_id: QMessageBox.information(dialog, "提示", "根目录不能重命名。"); return
                current = kb_tree.currentItem(); name, accepted = QInputDialog.getText(dialog, "重命名文件夹", "新名称", text=current.text(0) if current else "")
                if accepted and name.strip():
                    try: kb.rename_folder(folder_id, name); refresh_folder_tree(); refresh_kb_list(); refresh_profile_trees()
                    except Exception as exc: QMessageBox.warning(dialog, "重命名失败", str(exc))

            def delete_current_folder() -> None:
                folder_id = selected_folder_id()
                if not folder_id: QMessageBox.information(dialog, "提示", "根目录不能删除。"); return
                current = kb_tree.currentItem()
                answer = QMessageBox.question(dialog, "删除文件夹", f"确认删除“{current.text(0) if current else ''}”及其知识库内容？\n原始电脑文件不会被删除。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer == QMessageBox.Yes:
                    try:
                        removed = kb.delete_folder(folder_id); self.settings.knowledge_base.enabled_document_ids = [item for item in self.settings.knowledge_base.enabled_document_ids if item not in removed]; refresh_folder_tree(); refresh_kb_list(); refresh_profile_trees(); start_production_sync("文件夹已删除")
                    except Exception as exc: QMessageBox.warning(dialog, "删除失败", str(exc))

            def add_files() -> None:
                paths, _ = QFileDialog.getOpenFileNames(dialog, "选择知识库文件", "", "参考资料 (*.pdf *.doc *.docx)")
                if not paths: return
                worker = KnowledgeBaseImportWorker(kb, paths, folder_id=selected_folder_id(), chunk_size=self.settings.knowledge_base.chunk_size, chunk_overlap=self.settings.knowledge_base.chunk_overlap)
                self._knowledge_base_worker = worker; add_kb.setEnabled(False); cancel_kb.setEnabled(True)
                def done(records: list[Any]) -> None:
                    for record in records:
                        if record.status == "ready" and record.document_id not in self.settings.knowledge_base.enabled_document_ids: self.settings.knowledge_base.enabled_document_ids.append(record.document_id)
                    if settings_dialog_open():
                        cancel_kb.setEnabled(False)
                        refresh_kb_list()
                    ready_count = sum(1 for record in records if record.status == "ready")
                    if not ready_count:
                        if settings_dialog_open():
                            add_kb.setEnabled(True)
                            kb_status.setText("导入未产生可用文档，未启动生产 RAG 同步")
                        return
                    start_production_sync(f"知识库导入完成：{ready_count} 个文件")
                def failed(message: str) -> None:
                    if settings_dialog_open():
                        add_kb.setEnabled(True); cancel_kb.setEnabled(False); kb_status.setText(message); QMessageBox.warning(dialog, "导入失败", message); refresh_kb_list()
                    else:
                        self.status_label.setText("知识库后台导入失败；已完成的文件仍会保留")
                def cancelled(message: str) -> None:
                    if settings_dialog_open():
                        add_kb.setEnabled(True); cancel_kb.setEnabled(False); kb_status.setText(f"{message}；已完成文件已保留"); refresh_kb_list()
                    else:
                        self.status_label.setText("知识库后台导入已停止；已完成文件仍会保留")
                    # Completed files remain authoritative even when the batch is stopped.
                    if kb.list_documents():
                        start_production_sync("导入已停止，正在同步已完成文件")
                def progress(path: str, page: int, total: int, backend: str) -> None:
                    name = Path(path).name
                    status = f"正在导入：{name}" if not total else f"正在导入：{name}（第 {page}/{total} 页）"
                    kb_status.setText(f"{status}；{backend}" if backend else status)
                worker.completed.connect(done); worker.failed.connect(failed); worker.cancelled.connect(cancelled); worker.progress_detail.connect(progress); worker.start()

            def start_production_sync(prefix: str) -> None:
                running = getattr(self, "_knowledge_base_sync_worker", None)
                if running is not None and running.isRunning():
                    self._knowledge_base_sync_pending = True
                    self._knowledge_base_sync_pending_prefix = prefix
                    set_kb_status("知识库已变更；当前同步完成后将自动按最新内容再次同步")
                    return
                self._knowledge_base_sync_pending = False
                self._knowledge_base_sync_pending_prefix = ""
                worker = KnowledgeBaseProductionSyncWorker(kb_root)
                self._knowledge_base_sync_worker = worker
                # Saving settings must not wait for, cancel, or disable this
                # long-running worker.  It is owned by the main window.
                set_kb_status(f"{prefix}；正在自动重建、验证并激活生产 RAG")

                def sync_done(result: dict[str, Any]) -> None:
                    ids = {str(item) for item in result.get("active_document_ids", []) if str(item)}
                    scope_result = synchronize_settings_scopes(self.settings, kb, ids)
                    self.settings_store.save(self.settings)
                    if settings_dialog_open():
                        refresh_kb_list()
                        refresh_profile_trees()
                        add_kb.setEnabled(True)
                    snapshot = active_rag_snapshot()
                    semantic_status = snapshot.get("semantic") or {}
                    semantic_manifest = semantic_status.get("manifest") or {}
                    if settings_dialog_open():
                        kb_active_status.setText(
                            "生产 active RAG："
                            f"{len(ids)} 个有效文档；存储范围 {snapshot['storage_scope'] or 'unknown'}；"
                            f"语义检索 {'已启用' if semantic_status.get('healthy') and semantic_status.get('configured') else '未启用'}"
                            f"（{semantic_manifest.get('model_name') or 'unknown'}，{semantic_status.get('retrieval_count', 0)} 条）；"
                            "重排 rerank.v1 已启用；"
                            f"manifest {snapshot['manifest_path'] or '不可用'}；GUI 范围已同步"
                        )
                    excluded = sum(
                        len(item.get("excluded_not_active", []))
                        for item in scope_result["profiles"].values()
                    )
                    suffix = f"，已清理 {excluded} 个未激活文档 ID" if excluded else ""
                    if result.get("disabled"):
                        set_kb_status("知识库文件已全部删除，生产 RAG 已停用")
                    else:
                        set_kb_status(f"生产 RAG 自动同步完成{suffix}")
                    if not settings_dialog_open():
                        self.status_label.setText(
                            "知识库文件已全部删除，生产 RAG 已停用"
                            if result.get("disabled") else f"知识库后台同步完成{suffix}"
                        )

                def sync_failed(message: str) -> None:
                    set_kb_status(f"{message}；旧 active RAG 保持不变")
                    if settings_dialog_open():
                        add_kb.setEnabled(True)
                        QMessageBox.warning(dialog, "生产 RAG 同步失败", f"{message}\n旧 active RAG 保持不变。")
                    else:
                        self.status_label.setText("知识库后台同步失败；旧 active RAG 保持不变")

                def sync_finished() -> None:
                    if getattr(self, "_knowledge_base_sync_worker", None) is worker:
                        self._knowledge_base_sync_worker = None
                    if getattr(self, "_knowledge_base_sync_pending", False):
                        pending_prefix = str(getattr(self, "_knowledge_base_sync_pending_prefix", "") or "知识库已更新")
                        self._knowledge_base_sync_pending = False
                        self._knowledge_base_sync_pending_prefix = ""
                        start_production_sync(pending_prefix)

                worker.progress.connect(set_kb_status)
                worker.completed.connect(sync_done)
                worker.failed.connect(sync_failed)
                worker.finished.connect(sync_finished)
                worker.start()

            def cancel_import() -> None:
                worker = getattr(self, "_knowledge_base_worker", None)
                if worker is not None and worker.isRunning():
                    cancel_kb.setEnabled(False)
                    kb_status.setText("正在停止导入，等待当前页结束…")
                    worker.stop()

            cancel_kb.clicked.connect(cancel_import)

            def remove_files() -> None:
                selected_items = kb_list.selectedItems()
                if not selected_items:
                    return
                answer = QMessageBox.question(dialog, "删除文件", f"确认从知识库中删除选中的 {len(selected_items)} 项？\n原始电脑文件不会被删除。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
                for item in selected_items:
                    document_id = str(item.data(0, Qt.UserRole) or ""); kb.remove_document(document_id)
                    self.settings.knowledge_base.enabled_document_ids = [value for value in self.settings.knowledge_base.enabled_document_ids if value != document_id]
                refresh_kb_list()
                start_production_sync("知识库文件已删除")

            def open_document(item: QTreeWidgetItem, _column: int = 0) -> None:
                path = Path(str(item.data(0, Qt.UserRole + 1) or ""))
                if path.is_file() and os.name == "nt": os.startfile(str(path))

            def file_menu(position: Any) -> None:
                item = kb_list.itemAt(position)
                if item is not None and item not in kb_list.selectedItems():
                    kb_list.clearSelection(); item.setSelected(True); kb_list.setCurrentItem(item)
                menu = QMenu(kb_list)
                open_action = menu.addAction("打开")
                delete_action = menu.addAction("从知识库删除")
                open_action.setEnabled(item is not None)
                delete_action.setEnabled(bool(kb_list.selectedItems()))
                selected = menu.exec(kb_list.viewport().mapToGlobal(position))
                if selected == open_action and item is not None:
                    open_document(item)
                elif selected == delete_action:
                    remove_files()

            def folder_menu(position: Any) -> None:
                item = kb_tree.itemAt(position)
                if item: kb_tree.setCurrentItem(item)
                menu = QMenu(kb_tree); a = menu.addAction("新建子文件夹"); b = menu.addAction("重命名"); c = menu.addAction("删除文件夹"); selected = menu.exec(kb_tree.viewport().mapToGlobal(position))
                if selected == a: create_folder()
                elif selected == b: rename_current_folder()
                elif selected == c: delete_current_folder()

            new_folder.clicked.connect(create_folder); add_kb.clicked.connect(add_files); rename_folder.clicked.connect(rename_current_folder); delete_folder.clicked.connect(delete_current_folder); remove_kb.clicked.connect(remove_files); kb_tree.currentItemChanged.connect(lambda *_: refresh_kb_list()); kb_tree.customContextMenuRequested.connect(folder_menu); kb_list.customContextMenuRequested.connect(file_menu); kb_list.itemDoubleClicked.connect(open_document)
            refresh_folder_tree(); refresh_kb_list()

            profile_display_names = {"損傷分析報告": "损伤分析报告", "施工方案": "施工方案"}

            def build_profile_page(profile_name: str) -> QWidget:
                page, layout = add_page(profile_name, profile_display_names[profile_name])
                profile = self.settings.generation_profiles[profile_name]
                prompt = QPlainTextEdit(profile.prompt); prompt.setMinimumHeight(220); profile_edits[profile_name] = prompt
                layout.addWidget(QLabel("提示词")); layout.addWidget(prompt)
                model = QLineEdit(profile.model); profile_models[profile_name] = model; layout.addWidget(QLabel("模型（留空使用 API 模型）")); layout.addWidget(model)
                tree = QTreeWidget(); tree.setHeaderLabels(["一级知识库文件夹（多选，留空使用整个知识库）"]); tree.setSelectionMode(QTreeWidget.MultiSelection); tree.setMinimumHeight(180); profile_folder_trees[profile_name] = tree
                layout.addWidget(tree)
                folder_items: dict[str, QTreeWidgetItem] = {}
                for folder in kb.list_folders():
                    item = QTreeWidgetItem([folder.name]); item.setData(0, Qt.UserRole, folder.folder_id)
                    parent = folder_items.get(folder.parent_id)
                    if parent is None:
                        tree.addTopLevelItem(item)
                    else:
                        parent.addChild(item)
                    folder_items[folder.folder_id] = item
                    if folder.parent_id:
                        item.setDisabled(True)
                    if folder.folder_id in profile.knowledge_base_folder_ids:
                        item.setSelected(True)
                output_note = QLabel("本功能输出经过校验的 JSON 和 Markdown；Word 模板仅保留为历史兼容数据。")
                output_note.setObjectName("mutedText"); output_note.setWordWrap(True); layout.addWidget(output_note); layout.addStretch(1)
                return page

            for name in PROFILE_NAMES: build_profile_page(name)
            render_page, render_layout = add_page("修复渲染图", "修复渲染图提示词")
            render_prompt_edit = QPlainTextEdit(self.settings.render_prompt); render_prompt_edit.setMinimumHeight(260); render_layout.addWidget(QLabel("提示词")); render_layout.addWidget(render_prompt_edit); render_layout.addStretch(1)

            def refresh_profile_trees() -> None:
                for profile_name, tree in profile_folder_trees.items():
                    current = {str(item.data(0, Qt.UserRole)) for item in tree.selectedItems() if item.parent() is None}; current.update(self.settings.generation_profiles[profile_name].knowledge_base_folder_ids); tree.clear()
                    items: dict[str, QTreeWidgetItem] = {}
                    for folder in kb.list_folders():
                        item = QTreeWidgetItem([folder.name]); item.setData(0, Qt.UserRole, folder.folder_id)
                        parent = items.get(folder.parent_id)
                        if parent is None:
                            tree.addTopLevelItem(item)
                        else:
                            parent.addChild(item)
                        items[folder.folder_id] = item
                        if folder.parent_id:
                            item.setDisabled(True)
                        item.setSelected(folder.folder_id in current)

            nav.currentRowChanged.connect(stack.setCurrentIndex); nav.setCurrentRow(0)
            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Save).setText("保存")
            buttons.button(QDialogButtonBox.Save).setObjectName("settingsSaveButton")
            buttons.button(QDialogButtonBox.Cancel).setText("取消")
            root.addWidget(buttons)
            buttons.rejected.connect(dialog.reject); buttons.accepted.connect(dialog.accept)
            if dialog.exec() != QDialog.Accepted: return
            for name, editor in profile_edits.items():
                profile = self.settings.generation_profiles[name]; profile.prompt = editor.toPlainText().strip(); profile.model = profile_models[name].text().strip(); profile.knowledge_base_folder_ids = [str(item.data(0, Qt.UserRole)) for item in profile_folder_trees[name].selectedItems() if item.parent() is None]
                resolve_profile_folder_scope(profile, kb, active_document_ids())
            self.settings.knowledge_base.enabled_document_ids = sorted(active_document_ids())
            self.settings.render_prompt = render_prompt_edit.toPlainText().strip()
            self._save_api_config({
                "responses_url": responses_url.text().strip(),
                "responses_key": responses_key.text().strip(),
                "responses_model": responses_model.text().strip(),
                "repair_render_provider": str(render_provider.currentData()),
                "fhl_url": fhl_url.text().strip(),
                "fhl_key": fhl_key.text().strip(),
                "siliconflow_url": siliconflow_url.text().strip(),
                "siliconflow_key": siliconflow_key.text().strip(),
                "siliconflow_model": siliconflow_model.text().strip(),
            })
            self.status_label.setText("设置已保存。")

        def open_settings(self) -> None:
            return self._open_settings_vertical()

        def open_assistant(self) -> None:
            """Open the persistent read-only multi-source assistant dialog."""
            try:
                from runtime.assistant.dialog import AssistantDialog
            except Exception as exc:
                QMessageBox.warning(self, "问答助手不可用", f"问答助手界面加载失败：{type(exc).__name__}: {exc}")
                return
            dialog = getattr(self, "_assistant_dialog", None)
            if dialog is not None and dialog.isVisible():
                dialog.raise_()
                dialog.activateWindow()
                return
            dialog = AssistantDialog(
                self,
                api_config=dict(self.api_config),
                settings=self.settings,
                current_output_getter=lambda: self.output_dir,
            )
            self._assistant_dialog = dialog
            dialog.finished.connect(lambda _result: setattr(self, "_assistant_dialog", None))
            dialog.show()

        def _open_settings_legacy(self) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("设置")
            dialog.resize(1000, 760)
            root = QVBoxLayout(dialog)
            tabs = QTabWidget(dialog)
            tabs.setDocumentMode(True)
            tabs.setUsesScrollButtons(False)
            tabs.setElideMode(Qt.ElideNone)
            tabs.setMinimumHeight(600)
            root.addWidget(tabs)

            api_page = QWidget(); form = QFormLayout(api_page)
            responses_url = QLineEdit(self.api_config["responses_url"])
            responses_key = QLineEdit(self.api_config["responses_key"])
            responses_key.setEchoMode(QLineEdit.Password)
            responses_model = QLineEdit(self.api_config["responses_model"])
            render_provider = QComboBox()
            render_provider.addItem("FHL", "fhl")
            render_provider.addItem("硅基流动", "siliconflow")
            provider_index = render_provider.findData(self.api_config.get("repair_render_provider", "fhl"))
            render_provider.setCurrentIndex(max(0, provider_index))
            fhl_url = QLineEdit(self.api_config["fhl_url"])
            fhl_key = QLineEdit(self.api_config["fhl_key"])
            fhl_key.setEchoMode(QLineEdit.Password)
            siliconflow_url = QLineEdit(self.api_config["siliconflow_url"])
            siliconflow_key = QLineEdit(self.api_config["siliconflow_key"])
            siliconflow_key.setEchoMode(QLineEdit.Password)
            siliconflow_model = QLineEdit(self.api_config["siliconflow_model"])
            form.addRow("Responses API URL", responses_url)
            form.addRow("Responses API Key", responses_key)
            form.addRow("Responses 模型", responses_model)
            form.addRow("修复渲染平台", render_provider)
            form.addRow("FHL Image Gen API URL", fhl_url)
            form.addRow("FHL Image Gen API Key", fhl_key)
            form.addRow("硅基流动 API URL", siliconflow_url)
            form.addRow("硅基流动 API Key", siliconflow_key)
            form.addRow("硅基流动图像模型", siliconflow_model)
            note = QLabel("密鑰僅儲存在本機設定檔，介面以圓點遮罩；Responses、FHL 生圖與硅基流動分別使用各自獨立的 URL 和 Key，不會互相替代。")
            note.setWordWrap(True)
            form.addRow("說明", note)
            tabs.addTab(api_page, "API")

            kb_page = QWidget(); kb_layout = QVBoxLayout(kb_page)
            kb_title = QLabel("知识库")
            kb_title.setObjectName("settingsPageTitle")
            kb_layout.addWidget(kb_title)
            kb_layout.addWidget(QLabel("可新建多级文件夹分类资料；三个生成模块可多选一级文件夹，留空则使用整个知识库。"))
            kb_tree = QTreeWidget()
            kb_tree.setHeaderLabels(["知识库文件夹（选中后添加文件）"])
            kb_tree.setSelectionMode(QTreeWidget.SingleSelection)
            kb_tree.setContextMenuPolicy(Qt.CustomContextMenu)
            kb_tree.setMinimumHeight(180)
            kb_list = QListWidget()
            kb_list.setMinimumHeight(240)
            kb_root = resolve_user_path(self.settings.knowledge_base.root_dir, default=user_knowledge_base_root())
            kb = KnowledgeBase(kb_root)
            try:
                for record in kb.list_documents():
                    kb_list.addItem(f"{record.name}  [{record.status}]  {record.document_id}")
            except Exception as exc:
                kb_list.addItem(f"知识库不可用：{type(exc).__name__}")
            kb_splitter = QSplitter(Qt.Vertical)
            kb_splitter.addWidget(kb_tree)
            kb_splitter.addWidget(kb_list)
            kb_splitter.setStretchFactor(0, 1)
            kb_splitter.setStretchFactor(1, 2)
            kb_splitter.setSizes([220, 360])
            kb_layout.addWidget(kb_splitter, 1)
            kb_buttons = QHBoxLayout()
            new_folder = QPushButton("新建文件夹")
            add_kb = QPushButton("添加文件")
            remove_kb = QPushButton("移除选中")
            kb_buttons.addWidget(new_folder); kb_buttons.addWidget(add_kb); kb_buttons.addWidget(remove_kb); kb_buttons.addStretch(1)
            kb_layout.addLayout(kb_buttons)

            def refresh_folder_tree() -> None:
                kb_tree.clear()
                root_item = QTreeWidgetItem(["知识库（全部内容）"])
                root_item.setData(0, Qt.UserRole, "")
                kb_tree.addTopLevelItem(root_item)
                items: dict[str, QTreeWidgetItem] = {"": root_item}
                for folder in kb.list_folders():
                    item = QTreeWidgetItem([folder.name])
                    item.setData(0, Qt.UserRole, folder.folder_id)
                    parent = items.get(folder.parent_id, root_item)
                    parent.addChild(item)
                    items[folder.folder_id] = item
                kb_tree.expandAll()

            def refresh_profile_folder_trees() -> None:
                for profile_name, tree in profile_folder_trees.items():
                    selected_ids = {
                        str(item.data(0, Qt.UserRole))
                        for item in tree.selectedItems()
                        if item.parent() is None
                    }
                    tree.clear()
                    profile = self.settings.generation_profiles[profile_name]
                    selected_ids.update(profile.knowledge_base_folder_ids)
                    items: dict[str, QTreeWidgetItem] = {}
                    for folder in kb.list_folders():
                        item = QTreeWidgetItem([folder.name])
                        item.setData(0, Qt.UserRole, folder.folder_id)
                        parent = items.get(folder.parent_id)
                        if parent is None:
                            tree.addTopLevelItem(item)
                        else:
                            parent.addChild(item)
                        items[folder.folder_id] = item
                        if folder.parent_id:
                            item.setDisabled(True)
                        item.setSelected(folder.folder_id in selected_ids)
                    tree.expandAll()

            refresh_folder_tree()

            def selected_folder_id() -> str:
                item = kb_tree.currentItem()
                return str(item.data(0, Qt.UserRole) or "") if item else ""

            def refresh_kb_list() -> None:
                kb_list.clear()
                try:
                    records = kb.list_documents_in_folders([selected_folder_id()]) if selected_folder_id() else kb.list_documents()
                    for record in records:
                        item = QListWidgetItem(f"{record.name}  [{record.status}]  {record.document_id}")
                        item.setData(Qt.UserRole, record.document_id)
                        item.setData(Qt.UserRole + 1, record.path)
                        kb_list.addItem(item)
                except Exception as exc:
                    kb_list.addItem(f"知识库不可用：{type(exc).__name__}")

            refresh_kb_list()

            def add_knowledge_files() -> None:
                paths, _ = QFileDialog.getOpenFileNames(
                    dialog,
                    "选择知识库文件",
                    "",
                    "参考资料 (*.pdf *.doc *.docx)",
                )
                if not paths:
                    return
                selected = kb_tree.currentItem()
                folder_id = str(selected.data(0, Qt.UserRole) or "") if selected else ""
                add_kb.setEnabled(False)
                self._knowledge_base_worker = KnowledgeBaseImportWorker(
                    kb,
                    paths,
                    folder_id=folder_id,
                    chunk_size=self.settings.knowledge_base.chunk_size,
                    chunk_overlap=self.settings.knowledge_base.chunk_overlap,
                )

                def on_imported(records: list[Any]) -> None:
                    for record in records:
                        if record.status == "ready" and record.document_id not in self.settings.knowledge_base.enabled_document_ids:
                            self.settings.knowledge_base.enabled_document_ids.append(record.document_id)
                    add_kb.setEnabled(True)
                    refresh_kb_list()
                    if any(record.status == "ready" for record in records):
                        self.status_label.setText("知识库已导入；请使用新版知识库页面完成生产 RAG 自动同步")

                def on_import_failed(message: str) -> None:
                    add_kb.setEnabled(True)
                    QMessageBox.warning(dialog, "知识库导入失败", message)
                    refresh_kb_list()

                self._knowledge_base_worker.completed.connect(on_imported)
                self._knowledge_base_worker.failed.connect(on_import_failed)
                self._knowledge_base_worker.start()

            def create_knowledge_folder() -> None:
                parent_item = kb_tree.currentItem()
                parent_id = str(parent_item.data(0, Qt.UserRole) or "") if parent_item else ""
                name, accepted = QInputDialog.getText(dialog, "新建文件夹", "文件夹名称")
                if not accepted or not name.strip():
                    return
                try:
                    kb.create_folder(name, parent_id=parent_id)
                    refresh_folder_tree()
                    refresh_profile_folder_trees()
                except Exception as exc:
                    QMessageBox.warning(dialog, "文件夹创建失败", str(exc))

            def remove_knowledge_file() -> None:
                item = kb_list.currentItem()
                if item is None:
                    return
                document_id = str(item.data(Qt.UserRole) or item.text().rsplit(" ", 1)[-1])
                kb.remove_document(document_id)
                self.settings.knowledge_base.enabled_document_ids = [
                    value for value in self.settings.knowledge_base.enabled_document_ids if value != document_id
                ]
                refresh_kb_list()

            def rename_selected_folder() -> None:
                folder_id = selected_folder_id()
                if not folder_id:
                    QMessageBox.information(dialog, "无法重命名", "根目录不能重命名。")
                    return
                item = kb_tree.currentItem()
                name, accepted = QInputDialog.getText(dialog, "重命名文件夹", "新名称", text=item.text(0) if item else "")
                if not accepted or not name.strip():
                    return
                try:
                    kb.rename_folder(folder_id, name)
                    refresh_folder_tree(); refresh_kb_list(); refresh_profile_folder_trees()
                except Exception as exc:
                    QMessageBox.warning(dialog, "重命名失败", str(exc))

            def delete_selected_folder() -> None:
                folder_id = selected_folder_id()
                if not folder_id:
                    QMessageBox.information(dialog, "无法删除", "根目录不能删除。")
                    return
                item = kb_tree.currentItem()
                answer = QMessageBox.question(dialog, "删除文件夹", f"确认从知识库中删除“{item.text(0) if item else ''}”及其全部内容？\n原始电脑文件不会被删除。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
                try:
                    removed = kb.delete_folder(folder_id, recursive=True)
                    self.settings.knowledge_base.enabled_document_ids = [doc_id for doc_id in self.settings.knowledge_base.enabled_document_ids if doc_id not in removed]
                    refresh_folder_tree(); refresh_kb_list(); refresh_profile_folder_trees()
                except Exception as exc:
                    QMessageBox.warning(dialog, "删除失败", str(exc))

            def show_folder_menu(position: Any) -> None:
                item = kb_tree.itemAt(position)
                if item is not None:
                    kb_tree.setCurrentItem(item)
                menu = QMenu(kb_tree)
                new_action = menu.addAction("新建子文件夹")
                rename_action = menu.addAction("重命名")
                delete_action = menu.addAction("删除文件夹")
                chosen = menu.exec(kb_tree.viewport().mapToGlobal(position))
                if chosen == new_action:
                    create_knowledge_folder()
                elif chosen == rename_action:
                    rename_selected_folder()
                elif chosen == delete_action:
                    delete_selected_folder()

            add_kb.clicked.connect(add_knowledge_files)
            remove_kb.clicked.connect(remove_knowledge_file)
            new_folder.clicked.connect(create_knowledge_folder)
            kb_tree.currentItemChanged.connect(lambda _current, _previous: refresh_kb_list())
            kb_tree.customContextMenuRequested.connect(show_folder_menu)

            def open_selected_document(item: QListWidgetItem) -> None:
                path = Path(str(item.data(Qt.UserRole + 1) or ""))
                if not path.is_file():
                    QMessageBox.warning(dialog, "文件不存在", f"找不到原始文件：{path}")
                    return
                try:
                    if os.name == "nt":
                        os.startfile(str(path))
                    else:
                        import subprocess
                        subprocess.Popen(["xdg-open", str(path)])
                except OSError as exc:
                    QMessageBox.warning(dialog, "无法打开文件", str(exc))

            kb_list.itemDoubleClicked.connect(open_selected_document)
            tabs.addTab(kb_page, "知识库")

            profile_edits: dict[str, QPlainTextEdit] = {}
            profile_models: dict[str, QLineEdit] = {}
            profile_folder_trees: dict[str, QTreeWidget] = {}
            profile_display_names = {
                "損傷分析報告": "损伤分析报告",
                "施工方案": "施工方案",
            }
            for profile_name in PROFILE_NAMES:
                page = QWidget(); profile_form = QFormLayout(page)
                profile = self.settings.generation_profiles[profile_name]
                prompt_edit = QPlainTextEdit(profile.prompt); prompt_edit.setMinimumHeight(180)
                profile_edits[profile_name] = prompt_edit
                profile_form.addRow("提示词", prompt_edit)
                model_edit = QLineEdit(profile.model)
                profile_models[profile_name] = model_edit
                profile_form.addRow("模型（留空使用 API 模型）", model_edit)
                scope_tree = QTreeWidget()
                scope_tree.setHeaderLabels(["可选一级文件夹（多选，空选=整个知识库）"])
                scope_tree.setSelectionMode(QTreeWidget.MultiSelection)
                scope_items: dict[str, QTreeWidgetItem] = {}
                for folder in kb.list_folders():
                    item = QTreeWidgetItem([folder.name])
                    item.setData(0, Qt.UserRole, folder.folder_id)
                    parent = scope_items.get(folder.parent_id)
                    if parent is None:
                        scope_tree.addTopLevelItem(item)
                    else:
                        parent.addChild(item)
                    scope_items[folder.folder_id] = item
                    if folder.parent_id:
                        item.setDisabled(True)
                    if folder.folder_id in profile.knowledge_base_folder_ids:
                        item.setSelected(True)
                scope_tree.expandAll()
                scope_tree.setMinimumHeight(180)
                profile_folder_trees[profile_name] = scope_tree
                profile_form.addRow("知识库范围", scope_tree)
                output_note = QLabel("本功能输出经过校验的 JSON 和 Markdown；Word 模板仅保留为历史兼容数据。")
                output_note.setWordWrap(True)
                profile_form.addRow("输出格式", output_note)
                profile_form.addRow("说明", QLabel("生成时会保留结构化检测证据，并只使用所选知识库片段。"))
                tabs.addTab(page, profile_display_names.get(profile_name, profile_name))

            tabs.currentChanged.connect(lambda _index: refresh_profile_folder_trees())

            render_page = QWidget(); render_layout = QFormLayout(render_page)
            render_prompt_edit = QPlainTextEdit(self.settings.render_prompt)
            render_prompt_edit.setMinimumHeight(180)
            render_layout.addRow("修复渲染图提示词", render_prompt_edit)
            tabs.addTab(render_page, "修复渲染图")

            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            root.addWidget(buttons)
            if dialog.exec() != QDialog.Accepted:
                return
            for profile_name, editor in profile_edits.items():
                self.settings.generation_profiles[profile_name].prompt = editor.toPlainText().strip()
                self.settings.generation_profiles[profile_name].model = profile_models[profile_name].text().strip()
                self.settings.generation_profiles[profile_name].knowledge_base_folder_ids = [
                    str(item.data(0, Qt.UserRole))
                    for item in profile_folder_trees[profile_name].selectedItems()
                    if item.parent() is None
                ]
                resolve_profile_folder_scope(
                    self.settings.generation_profiles[profile_name],
                    kb,
                    active_document_ids(),
                )
            self.settings.knowledge_base.enabled_document_ids = sorted(active_document_ids())
            self.settings.render_prompt = render_prompt_edit.toPlainText().strip()
            try:
                self._save_api_config({
                    "responses_url": responses_url.text().strip(),
                    "responses_key": responses_key.text().strip(),
                    "responses_model": responses_model.text().strip(),
                    "repair_render_provider": str(render_provider.currentData()),
                    "fhl_url": fhl_url.text().strip(),
                    "fhl_key": fhl_key.text().strip(),
                    "siliconflow_url": siliconflow_url.text().strip(),
                    "siliconflow_key": siliconflow_key.text().strip(),
                    "siliconflow_model": siliconflow_model.text().strip(),
                })
            except ValueError as exc:
                QMessageBox.warning(self, "设置未保存", str(exc))
                return
            self.status_label.setText("设置已儲存。")

        def open_api_settings(self) -> None:
            """Compatibility alias for plugins/tests that still call the old entry point."""
            self.open_settings()

        def _build_ui(self) -> None:
            root = QWidget(self)
            root.setObjectName("workbenchContent")
            root.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            root.setMinimumSize(0, 0)
            self.workbench_content = root
            outer = QVBoxLayout(root)
            outer.setSizeConstraint(QLayout.SetNoConstraint)
            outer.setContentsMargins(16, 11, 16, 11)
            outer.setSpacing(12)
            self.workbench_outer = outer

            header = QHBoxLayout()
            brand = QLabel("結構損傷自動化檢測系統")
            brand.setObjectName("brand")
            header.addWidget(brand)
            self.version_label = QLabel("v2.1.0")
            self.version_label.setObjectName("versionText")
            header.addWidget(self.version_label)
            header.addStretch(1)
            self.api_button = QPushButton("设置")
            self.api_button.setMinimumWidth(120)
            self.api_button.setIcon(QApplication.style().standardIcon(QStyle.SP_FileDialogDetailedView))
            self.api_button.clicked.connect(self.open_settings)
            header.addWidget(self.api_button)
            self.assistant_button = QPushButton("问答助手")
            self.assistant_button.setMinimumWidth(120)
            self.assistant_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MessageBoxInformation))
            self.assistant_button.clicked.connect(self.open_assistant)
            header.addWidget(self.assistant_button)
            self.system_status = QLabel("● 系統狀態：待命")
            self.system_status.setObjectName("systemStatus")
            self.system_status.setMinimumWidth(180)
            self.system_status.setVisible(False)
            outer.addLayout(header)

            body = QHBoxLayout()
            body.setSpacing(12)

            queue = QFrame(); queue.setObjectName("hudPanel"); ql = QVBoxLayout(queue)
            ql.setContentsMargins(14, 14, 14, 14); ql.setSpacing(10)
            qhead = QHBoxLayout()
            qtitle = QLabel("任務佇列"); qtitle.setObjectName("panelTitle"); qhead.addWidget(qtitle)
            qhead.addStretch(1)
            self.queue_count = QLabel("0 / 0"); self.queue_count.setObjectName("accentText"); qhead.addWidget(self.queue_count)
            qhead_widget = QWidget()
            qhead_widget.setObjectName("queueHeader")
            qhead_widget.setLayout(qhead)
            qhead_widget.setFixedHeight(32)
            qhead_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.queue_header = qhead_widget
            ql.addWidget(qhead_widget, 0)
            self.image_list = TaskQueueTable(); self.image_list.setMaximumHeight(160); self.image_list.currentTextChanged.connect(self.show_selected_overlay); ql.addWidget(self.image_list, 1)

            source_card = QFrame(); source_card.setObjectName("sourceCard"); source_layout = QVBoxLayout(source_card)
            source_card.setMaximumHeight(240)
            source_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            source_layout.setContentsMargins(10, 10, 10, 10); source_layout.setSpacing(8)
            source_head = QHBoxLayout()
            source_head.addWidget(QLabel("資料來源"))
            source_head.addStretch(1)
            choose = QPushButton("添加文件夹")
            choose.setObjectName("sourceToolButton")
            choose.setIcon(QApplication.style().standardIcon(QStyle.SP_DirOpenIcon))
            choose.clicked.connect(self.choose_folder)
            source_head.addWidget(choose)
            add_file = QPushButton("添加文件")
            add_file.setObjectName("sourceToolButton")
            add_file.setIcon(QApplication.style().standardIcon(QStyle.SP_FileIcon))
            add_file.clicked.connect(self.choose_files)
            remove_file = QPushButton("移除")
            remove_file.setObjectName("sourceToolButton")
            remove_file.setIcon(QApplication.style().standardIcon(QStyle.SP_TrashIcon))
            remove_file.clicked.connect(self.remove_selected_images)
            refresh_files = QPushButton("刷新")
            refresh_files.setObjectName("sourceToolButton")
            refresh_files.setIcon(QApplication.style().standardIcon(QStyle.SP_BrowserReload))
            refresh_files.clicked.connect(self.refresh_image_queue)
            self.source_action_buttons = (choose, add_file, remove_file, refresh_files)
            self._source_action_texts = {button: button.text() for button in self.source_action_buttons}
            # Keep the four source actions usable at wide viewports as well as
            # compact ones.  The header is narrower than the full workbench,
            # so letting each button shrink below its size hint makes the
            # labels clip and breaks the adaptive-layout contract.
            for button in self.source_action_buttons:
                button.setFixedHeight(46)
                button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
                button.setToolTip(button.text())
            for button in (add_file, remove_file, refresh_files):
                source_head.addWidget(button)
            source_head.setSpacing(4)
            source_layout.addLayout(source_head)
            self.folder_edit = QLineEdit(); self.folder_edit.setReadOnly(True); self.folder_edit.setPlaceholderText("尚未選擇損傷圖片資料夾")
            source_layout.addWidget(self.folder_edit)
            self.source_count = QLabel("共 0 个文件")
            self.source_count.setObjectName("mutedText")
            source_layout.addWidget(self.source_count)
            model_label = QLabel("識別模型")
            model_label.setObjectName("mutedText")
            source_layout.addWidget(model_label)
            self.model_edit = QLineEdit(str(DEFAULT_MODEL_PATH))
            self.model_edit.setReadOnly(True)
            source_layout.addWidget(self.model_edit)
            ql.addWidget(source_card)
            body.addWidget(queue, 1)

            preview_panel = QFrame(); preview_panel.setObjectName("hudPanel"); pl = QVBoxLayout(preview_panel)
            pl.setContentsMargins(14, 14, 14, 14); pl.setSpacing(10)
            phead = QHBoxLayout(); ptitle = QLabel("影像預覽"); ptitle.setObjectName("panelTitle"); phead.addWidget(ptitle); phead.addStretch(1)
            # Kept as a hidden compatibility handle; the approved visual removes this status text.
            self.preview_mode = QLabel()
            self.preview_mode.setVisible(False)
            pl.addLayout(phead)
            self.preview = QLabel("尚未執行損傷識別"); self.preview.setAlignment(Qt.AlignCenter); self.preview.setMinimumSize(0, 0); self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored); self.preview.setObjectName("previewCanvas"); pl.addWidget(self.preview, 1)
            self.preview_detail = QLabel("等待影像任務啟動"); self.preview_detail.setObjectName("mutedText"); pl.addWidget(self.preview_detail)
            self.preview_metadata = QLabel("—")
            self.preview_metadata.setObjectName("previewMetadata")
            pl.addWidget(self.preview_metadata)
            body.addWidget(preview_panel, 3)

            findings_panel = QFrame(); findings_panel.setObjectName("hudPanel"); fl = QVBoxLayout(findings_panel)
            fl.setContentsMargins(14, 14, 14, 14); fl.setSpacing(10)
            fhead = QHBoxLayout(); ftitle = QLabel("檢測結果"); ftitle.setObjectName("panelTitle"); fhead.addWidget(ftitle); fhead.addStretch(1)
            self.finding_summary = QLabel("已檢出 0 項損傷"); self.finding_summary.setObjectName("accentText"); fhead.addWidget(self.finding_summary)
            self.result_filter = QComboBox()
            self.result_filter.addItems(["全部类型"])
            self.result_filter.setObjectName("resultFilter")
            self.result_filter.setFixedWidth(120)
            self.result_filter.currentTextChanged.connect(self._apply_result_filter)
            fhead.addWidget(self.result_filter)
            fl.addLayout(fhead)
            self.findings_table = ProportionalTableWidget(DETECTION_RESULT_COLUMN_PROPORTIONS); self.findings_table.setHorizontalHeaderLabels(["编号", "文件名", "损伤类型", "置信度", "损伤等级", "状态"])
            findings_header = self.findings_table.horizontalHeader()
            findings_header.setDefaultAlignment(Qt.AlignCenter)
            self.findings_table.verticalHeader().setVisible(False)
            self.findings_table.horizontalHeaderItem(4).setToolTip(SCREENING_SEVERITY_TOOLTIP)
            fl.addWidget(self.findings_table, 1)
            self.finding_detail = QLabel("選擇檢測結果查看詳細證據"); self.finding_detail.setWordWrap(True); self.finding_detail.setObjectName("mutedText"); fl.addWidget(self.finding_detail)
            self.finding_legend = QLabel("共 0 条    I 轻微    II 中等    III 严重")
            self.finding_legend.setObjectName("mutedText")
            fl.addWidget(self.finding_legend)
            body.addWidget(findings_panel, 3)
            outer.addLayout(body, 5)

            project_panel = QFrame()
            project_panel.setObjectName("projectOverviewPanel")
            project_layout = QVBoxLayout(project_panel)
            project_layout.setContentsMargins(14, 8, 14, 10)
            project_layout.setSpacing(4)
            project_header = QHBoxLayout()
            self.project_overview_title = QLabel("项目概括（可选）")
            self.project_overview_title.setObjectName("projectOverviewTitle")
            self.project_overview_title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            project_header.addWidget(self.project_overview_title)
            project_header.addStretch(1)
            self.project_overview_counter = QLabel("0 / 4000")
            self.project_overview_counter.setObjectName("overviewCounter")
            project_header.addWidget(self.project_overview_counter)
            project_layout.addLayout(project_header)
            self.project_overview_edit = QPlainTextEdit()
            self.project_overview_edit.setPlaceholderText("填写项目背景、检测目的、环境说明或委托要求。")
            self.project_overview_edit.setMinimumHeight(76)
            self.project_overview_edit.setMaximumHeight(118)
            self.project_overview_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.project_overview_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.project_overview_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            self.project_overview_edit.textChanged.connect(self._refresh_project_overview_counter)
            project_layout.addWidget(self.project_overview_edit)
            outer.addWidget(project_panel, 0)

            progress_panel = QFrame()
            progress_panel.setObjectName("hudPanel")
            progress_layout = QHBoxLayout(progress_panel)
            progress_layout.setContentsMargins(14, 7, 14, 7)
            progress_layout.setSpacing(10)
            self.workflow_progress_label = QLabel("处理进度")
            self.workflow_progress_label.setObjectName("panelTitle")
            progress_layout.addWidget(self.workflow_progress_label)
            self.workflow_progress_bar = QProgressBar()
            self.workflow_progress_bar.setObjectName("workflowProgress")
            self.workflow_progress_bar.setRange(0, 100)
            self.workflow_progress_bar.setValue(0)
            self.workflow_progress_bar.setFormat("%p%")
            self.workflow_progress_bar.setTextVisible(True)
            progress_layout.addWidget(self.workflow_progress_bar, 1)
            self.review_report_button = QPushButton("审核分析报告")
            self.review_report_button.setObjectName("compactButton")
            self.review_report_button.setEnabled(False)
            self.review_report_button.setVisible(False)
            self.review_report_button.clicked.connect(self.handle_report_action)
            progress_layout.addWidget(self.review_report_button)
            self.review_construction_plan_button = QPushButton("审核施工方案")
            self.review_construction_plan_button.setObjectName("compactButton")
            self.review_construction_plan_button.setEnabled(False)
            self.review_construction_plan_button.setVisible(False)
            self.review_construction_plan_button.clicked.connect(
                self.open_construction_plan_review_dialog
            )
            progress_layout.addWidget(self.review_construction_plan_button)
            outer.addWidget(progress_panel, 0)

            status_row = QHBoxLayout()
            status_row.setObjectName("statusRow")
            status_panel = QFrame(); status_panel.setObjectName("hudPanel"); sl = QHBoxLayout(status_panel)
            sl.setContentsMargins(14, 12, 14, 12); sl.setSpacing(12)
            self.progress_ring = CircularProgressWidget()
            self.workflow_progress_bar.valueChanged.connect(self.progress_ring.setValue)
            self.recognition_progress = self.workflow_progress_bar
            sl.addWidget(self.progress_ring, 0, Qt.AlignCenter)
            status_metrics = QVBoxLayout()
            self.stage_status = QLabel("處理狀態\n待命"); self.stage_status.setObjectName("stageStatus"); status_metrics.addWidget(self.stage_status)
            self.progress_metrics = QLabel("已完成 0   处理中 0   等待中 0   异常 0   总计 0"); self.progress_metrics.setObjectName("metricsText"); status_metrics.addWidget(self.progress_metrics, 1)
            self.progress_metrics.setWordWrap(True)
            self.queue_stats = QLabel("佇列總數：0\n已完成：0\n處理中：0\n失败：0"); self.queue_stats.setObjectName("mutedText"); status_metrics.addWidget(self.queue_stats)
            sl.addLayout(status_metrics, 1)
            status_row.addWidget(status_panel, 2)
            current_panel = QFrame(); current_panel.setObjectName("hudPanel"); cl = QVBoxLayout(current_panel); cl.setContentsMargins(14, 12, 14, 12); cl.setSpacing(5); self.current_file_title = QLabel("当前文件"); self.current_file_title.setObjectName("panelTitle"); self.current_file_title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed); cl.addWidget(self.current_file_title)
            self.current_file_stack = QStackedWidget()
            image_details = QWidget(); image_layout = QVBoxLayout(image_details); image_layout.setContentsMargins(0, 0, 0, 0); image_layout.setSpacing(5)
            self.current_file = QLabel("—"); self.current_file.setObjectName("currentFile"); self.current_file.setWordWrap(True); image_layout.addWidget(self.current_file); self.current_file_type = QLabel("文件类型：—"); self.current_file_type.setObjectName("mutedText"); image_layout.addWidget(self.current_file_type); self.current_file_resolution = QLabel("分辨率：—"); self.current_file_resolution.setObjectName("mutedText"); image_layout.addWidget(self.current_file_resolution); self.current_file_size = QLabel("文件大小：—"); self.current_file_size.setObjectName("mutedText"); image_layout.addWidget(self.current_file_size); self.current_file_time = QLabel("拍摄时间：—"); self.current_file_time.setObjectName("mutedText"); image_layout.addWidget(self.current_file_time); self.speed_label = QLabel("处理速度：—   ETA：—"); self.speed_label.setObjectName("mutedText"); self.speed_label.setVisible(False); image_layout.addWidget(self.speed_label); self.gpu_status = QLabel(_resolve_yolo_device()[1]); self.gpu_status.setObjectName("gpuStatus"); self.gpu_status.setVisible(False); image_layout.addWidget(self.gpu_status)
            generation_details = QWidget(); generation_layout = QVBoxLayout(generation_details); generation_layout.setContentsMargins(0, 0, 0, 0); generation_layout.setSpacing(5)
            self.current_generation_file = QLabel("—"); self.current_generation_file.setObjectName("currentFile"); generation_layout.addWidget(self.current_generation_file)
            self.current_generation_status = QLabel("等待生成"); self.current_generation_status.setObjectName("mutedText"); generation_layout.addWidget(self.current_generation_status)
            self.current_generation_content = QPlainTextEdit(); self.current_generation_content.setObjectName("generationPreview"); self.current_generation_content.setReadOnly(True); self.current_generation_content.setLineWrapMode(QPlainTextEdit.WidgetWidth); self.current_generation_content.setMaximumBlockCount(2000); self.current_generation_content.setPlaceholderText("模型生成内容将在这里实时显示…"); generation_layout.addWidget(self.current_generation_content, 1)
            self.current_file_stack.addWidget(image_details); self.current_file_stack.addWidget(generation_details); cl.addWidget(self.current_file_stack, 1); status_row.addWidget(current_panel, 4)
            log_panel = QFrame()
            log_panel.setObjectName("hudPanel")
            ll = QVBoxLayout(log_panel)
            ll.setContentsMargins(14, 12, 14, 12)
            ll.setSpacing(5)
            self.event_log_title = QLabel("事件日志")
            self.event_log_title.setObjectName("panelTitle")
            self.event_log_title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            ll.addWidget(self.event_log_title)
            self.event_log = QPlainTextEdit()
            self.event_log.setReadOnly(True)
            self.event_log.setMaximumBlockCount(200)
            self.event_log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            ll.addWidget(self.event_log, 1)
            status_row.addWidget(log_panel, 5)
            self.actions_panel = QFrame(); self.actions_panel.setObjectName("actionsPanel"); actions_layout = QVBoxLayout(self.actions_panel); actions_layout.setContentsMargins(12, 10, 12, 10); actions_layout.setSpacing(8)
            self.actions_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            document_actions = QHBoxLayout(); document_actions.setContentsMargins(0, 0, 0, 0); document_actions.setSpacing(8)
            self.open_report_word_button = QPushButton("打开分析报告")
            self.open_report_word_button.setEnabled(False)
            self.open_report_word_button.clicked.connect(self.open_generated_report)
            document_actions.addWidget(self.open_report_word_button, 1)
            self.open_construction_plan_button = QPushButton("打开施工方案")
            self.open_construction_plan_button.setEnabled(False)
            self.open_construction_plan_button.clicked.connect(self.open_generated_construction_plan)
            document_actions.addWidget(self.open_construction_plan_button, 1)
            actions_layout.addLayout(document_actions)
            primary_actions = QHBoxLayout(); primary_actions.setContentsMargins(0, 0, 0, 0); primary_actions.setSpacing(8)
            self.start_button = QPushButton("启动")
            self.start_button.setObjectName("startButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaPlay))
            self.start_button.setMinimumWidth(150)
            self.start_button.setMinimumHeight(48)
            self.start_button.clicked.connect(self.toggle_workflow)
            primary_actions.addWidget(self.start_button, 3)
            self.cancel_button = self.start_button
            self.render_checkbox = QCheckBox("生成修复渲染图")
            self._render_checkbox_text = self.render_checkbox.text()
            self.render_checkbox.setToolTip(self._render_checkbox_text)
            self.render_checkbox.setObjectName("renderToggle")
            self.render_checkbox.setChecked(False)
            self.render_checkbox.setMinimumHeight(48)
            self.render_checkbox.setMinimumWidth(210)
            self.render_checkbox.toggled.connect(self._on_render_option_toggled)
            primary_actions.addWidget(self.render_checkbox, 2)
            actions_layout.addLayout(primary_actions)
            self.render_stop_button = QPushButton("停止剩餘渲染圖"); self.render_stop_button.setVisible(False)
            status_row.addWidget(self.actions_panel, 3)
            outer.addLayout(status_row, 0)
            self.status_label = QLabel("請選擇損傷圖片資料夾後開始自動處理。"); self.status_label.setObjectName("status"); self.status_label.setVisible(False); outer.addWidget(self.status_label)
            # Hidden compatibility widgets used by report/plan/render services.
            self.report_text = QLabel(); self.report_path_label = QLabel(); self.plan_table = QTableWidget(0, 5); self.plan_status_label = QLabel(); self.gallery = QListWidget()
            self.generate_report_button = QPushButton(); self.build_plan_button = QPushButton(); self.render_button = QPushButton()
            self.generate_report_button.setVisible(False); self.build_plan_button.setVisible(False); self.render_button.setVisible(False)
            self._hud_timer = QTimer(self); self._hud_timer.timeout.connect(self._refresh_hud_clock)
            self._responsive_layout_specs = [
                (outer, (16, 11, 16, 11), 12),
                (header, (0, 0, 0, 0), 6),
                (body, (0, 0, 0, 0), 12),
                (ql, (14, 14, 14, 14), 10),
                (source_layout, (10, 10, 10, 10), 8),
                (source_head, (0, 0, 0, 0), 4),
                (pl, (14, 14, 14, 14), 10),
                (fl, (14, 14, 14, 14), 10),
                (project_layout, (14, 8, 14, 10), 4),
                (progress_layout, (14, 7, 14, 7), 10),
                (status_row, (0, 0, 0, 0), 12),
                (sl, (14, 12, 14, 12), 12),
                (status_metrics, (0, 0, 0, 0), 5),
                (cl, (14, 12, 14, 12), 5),
                (image_layout, (0, 0, 0, 0), 5),
                (generation_layout, (0, 0, 0, 0), 5),
                (ll, (14, 12, 14, 12), 8),
                (actions_layout, (12, 10, 12, 10), 8),
                (document_actions, (0, 0, 0, 0), 8),
                (primary_actions, (0, 0, 0, 0), 8),
            ]
            self._responsive_widgets = {
                "queue": queue,
                "source_card": source_card,
                "preview_panel": preview_panel,
                "findings_panel": findings_panel,
                "project_panel": project_panel,
                "document_panel": progress_panel,
                "status_panel": status_panel,
                "current_panel": current_panel,
                "log_panel": log_panel,
            }
            self.workbench_scroll = None
            self.setCentralWidget(root)
            self._apply_responsive_layout(force=True)

        @staticmethod
        def _scaled_metric(value: int, scale: float, minimum: int = 0) -> int:
            return max(minimum, round(value * scale))

        def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
            super().resizeEvent(event)
            if not hasattr(self, "workbench_content") or self._layout_update_pending:
                return
            self._layout_update_pending = True
            QTimer.singleShot(0, self._apply_responsive_layout)

        def _apply_responsive_layout(self, *, force: bool = False) -> None:
            self._layout_update_pending = False
            if not hasattr(self, "workbench_content"):
                return
            viewport_width = max(1, self.width())
            viewport_height = max(1, self.height())
            raw_scale = min(viewport_width / 1680.0, viewport_height / 980.0)
            scale = round(max(0.60, min(1.08, raw_scale)) / 0.025) * 0.025
            signature = (
                scale,
                viewport_width >= 1850,
                viewport_width >= 1900,
                viewport_width >= 2200,
            )
            if not force and signature == self._layout_signature:
                return
            self._layout_signature = signature
            self._layout_scale = scale
            # The source card is a narrow fixed column even on a wide monitor.
            # Decide compact mode from its actual width as well as the global
            # viewport so the four source actions never get clipped.
            source_card_width = self._responsive_widgets.get("source_card").width() if hasattr(self, "_responsive_widgets") else 0
            compact = viewport_width < 1850 or scale < 0.90 or (
                viewport_width >= 2200 and source_card_width < 420
            )

            for layout, margins, spacing in self._responsive_layout_specs:
                left, top, right, bottom = margins
                layout.setContentsMargins(
                    self._scaled_metric(left, scale),
                    self._scaled_metric(top, scale),
                    self._scaled_metric(right, scale),
                    self._scaled_metric(bottom, scale),
                )
                layout.setSpacing(self._scaled_metric(spacing, scale, 2) if spacing else 0)

            for panel in self._responsive_widgets.values():
                panel.setMinimumSize(0, 0)
            for panel_name in ("queue", "preview_panel", "findings_panel"):
                self._responsive_widgets[panel_name].setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
            for panel_name in ("status_panel", "current_panel", "log_panel"):
                self._responsive_widgets[panel_name].setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)

            source_button_height = self._scaled_metric(46, scale, 28)
            for button, text in self._source_action_texts.items():
                button.setToolTip(text)
                button.setText("" if compact else text)
                button.setMinimumHeight(source_button_height)
                button.setMaximumHeight(source_button_height)
                if compact:
                    button.setFixedWidth(max(30, source_button_height))
                else:
                    button.setMinimumWidth(0)
                    button.setMaximumWidth(16777215)

            self.queue_header.setFixedHeight(self._scaled_metric(32, scale, 22))
            self.image_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
            self.image_list.setMaximumHeight(self._scaled_metric(160, scale, 88))
            self._responsive_widgets["source_card"].setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
            self._responsive_widgets["source_card"].setMaximumHeight(self._scaled_metric(240, scale, 142))
            field_height = self._scaled_metric(34, scale, 24)
            for field in (self.folder_edit, self.model_edit):
                field.setMinimumHeight(field_height)
                field.setMaximumHeight(field_height)

            self.preview.setMinimumSize(0, 0)
            self.result_filter.setFixedWidth(self._scaled_metric(120, scale, 84))
            self.project_overview_title.setFixedHeight(self._scaled_metric(24, scale, 18))
            self.project_overview_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.project_overview_edit.setMinimumHeight(self._scaled_metric(76, scale, 42))
            self.project_overview_edit.setMaximumHeight(self._scaled_metric(118, scale, 70))
            self.progress_ring.set_ui_scale(scale)
            self.progress_ring.setVisible(viewport_width >= 2200)
            self.current_file_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
            self.event_log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.render_checkbox.setText(self._render_checkbox_text)
            if viewport_width < 1900:
                self.start_button.setMinimumWidth(self._scaled_metric(100, scale, 64))
                self.render_checkbox.setMinimumWidth(self._scaled_metric(170, scale, 104))
            else:
                self.start_button.setMinimumWidth(self._scaled_metric(150, scale, 90))
                self.render_checkbox.setMinimumWidth(self._scaled_metric(210, scale, 126))

            self._apply_theme(scale)
            self.start_button.setMinimumWidth(max(self.start_button.minimumWidth(), self.start_button.sizeHint().width()))
            self.render_checkbox.setMinimumWidth(max(self.render_checkbox.minimumWidth(), self.render_checkbox.sizeHint().width()))
            self._sync_primary_action_heights()
            self.workbench_outer.invalidate()
            self.workbench_outer.activate()
            self.workbench_content.updateGeometry()

        def _refresh_hud_clock(self) -> None:
            if self.worker is not None and self.worker.isRunning():
                self.system_status.setText("● 系統狀態：執行中")

        def _refresh_project_overview_counter(self) -> None:
            if not hasattr(self, "project_overview_edit"):
                return
            count = len(self.project_overview_edit.toPlainText())
            self.project_overview_counter.setText(f"{count} / {PROJECT_OVERVIEW_MAX_CHARS}")
            self.project_overview_counter.setProperty("overLimit", count > PROJECT_OVERVIEW_MAX_CHARS)
            self.project_overview_counter.style().unpolish(self.project_overview_counter)
            self.project_overview_counter.style().polish(self.project_overview_counter)

        def _log_event(self, message: str) -> None:
            if hasattr(self, "event_log"):
                lines = self.event_log.toPlainText().splitlines()
                if lines and lines[-1] == message:
                    return
                self.event_log.appendPlainText(message)

        def _set_workflow_stage(
            self,
            stage: str,
            *,
            system_status: str,
            status_text: str,
            log_message: str | None = None,
            overall_progress: int | None = None,
        ) -> None:
            self.stage_status.setText(f"處理狀態\n{stage}")
            self.system_status.setText(f"● 系統狀態：{system_status}")
            self.status_label.setText(status_text)
            if overall_progress is not None:
                self.recognition_progress.setValue(max(0, min(100, int(overall_progress))))
            if log_message:
                self._log_event(log_message)

        def _refresh_hud_metrics(self, *, processing: bool | None = None) -> None:
            total = self.image_list.count()
            processed = len(self.live_results)
            failed = sum(1 for item in self.live_results.values() if item.get("status") == "failed")
            completed = max(0, processed - failed)
            pending = max(0, total - processed)
            if processing is None:
                processing = bool(self.worker is not None and self.worker.isRunning() and pending > 0)
            active = 1 if processing and pending > 0 else 0
            waiting = max(0, pending - active)

            finding_count = sum(
                len(item.get("damage_findings", []) or [])
                for item in self.live_results.values()
            )

            self.queue_count.setText(f"{processed} / {total}")
            self.queue_stats.setText(
                f"佇列總數：{total}\n已完成：{completed}\n失敗：{failed}"
            )
            self.progress_metrics.setText(
                f"已完成 {completed}   处理中 {active}   等待中 {waiting}   异常 {failed}   总计 {total}"
            )
            self.finding_summary.setText(f"已檢出 {finding_count} 項損傷")
            self.finding_legend.setText(f"共 {finding_count} 条    I 轻微    II 中等    III 严重")

        def _refresh_result_filter_options(self) -> None:
            current = self.result_filter.currentText() if hasattr(self, "result_filter") else "全部类型"
            kinds = sorted({
                self.findings_table.item(row, 2).text()
                for row in range(self.findings_table.rowCount())
                if self.findings_table.item(row, 2) is not None and self.findings_table.item(row, 2).text()
            })
            self.result_filter.blockSignals(True)
            self.result_filter.clear()
            self.result_filter.addItem("全部类型")
            self.result_filter.addItems(kinds)
            self.result_filter.setCurrentText(current if current in {"全部类型", *kinds} else "全部类型")
            self.result_filter.blockSignals(False)
            self._apply_result_filter(self.result_filter.currentText())

        def _apply_result_filter(self, selected: str) -> None:
            for row in range(self.findings_table.rowCount()):
                kind = self.findings_table.item(row, 2)
                self.findings_table.setRowHidden(row, selected != "全部类型" and (kind is None or kind.text() != selected))

        def _append_image_paths(self, paths: list[Path]) -> None:
            if not paths:
                return
            if self.selected_image_paths is None:
                base = self.folder or Path.cwd()
                existing = [base / self.image_list.item(row).text() for row in range(self.image_list.count())]
            else:
                existing = list(self.selected_image_paths)
            seen = {str(path.resolve()).casefold() for path in existing}
            for path in paths:
                resolved = path.resolve()
                if str(resolved).casefold() not in seen:
                    existing.append(resolved)
                    seen.add(str(resolved).casefold())
            self.selected_image_paths = sorted(existing, key=lambda path: path.name.casefold())
            if self.folder is None:
                self.folder = self.selected_image_paths[0].parent
            self.folder_edit.setText(str(self.folder))
            self.image_list.clear()
            self.image_list.addItems([path.name for path in self.selected_image_paths])
            self.source_count.setText(f"共 {len(self.selected_image_paths)} 个文件")
            self._refresh_hud_metrics(processing=False)

        def _update_current_file_details(self, name: str, item: dict[str, Any] | None = None) -> None:
            if getattr(self, "_generation_active", False):
                return
            self.current_file_stack.setCurrentIndex(0)
            self.current_file_title.setText("当前文件")
            path = Path(str((item or {}).get("image_path", ""))) if item else Path()
            if not path.is_file() and self.folder is not None:
                path = self.folder / name
            self.current_file.setText(name or "—")
            self.current_file_type.setText(f"文件类型：{path.suffix.upper().lstrip('.') or '—'}")
            self.current_file_size.setText(f"文件大小：{path.stat().st_size / (1024 * 1024):.1f} MB" if path.is_file() else "文件大小：—")
            self.current_file_time.setText(f"拍摄时间：{datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}" if path.is_file() else "拍摄时间：—")
            pixmap = _load_pixmap_unicode(path) if path.is_file() else QPixmap()
            self.current_file_resolution.setText(f"分辨率：{pixmap.width()} × {pixmap.height()}" if not pixmap.isNull() else "分辨率：—")

        def _begin_generation_preview(self, kind: str) -> None:
            self._generation_kind = kind
            self._generation_text = ""
            self._generation_active = True
            label, filename = {"report": ("损伤分析报告", "report.md"), "construction_plan": ("修复施工方案", "construction_plan.md")}.get(kind, ("AI生成内容", "—"))
            self.current_file_stack.setCurrentIndex(1)
            self.current_file_title.setText(label)
            self.current_generation_file.setText(filename)
            self.current_generation_status.setText("生成中：正在接收流式内容…")
            self.current_generation_content.clear()

        def _on_generation_update(self, kind: str, text: str) -> None:
            if not getattr(self, "_generation_active", False) or kind != self._generation_kind:
                return
            if text.startswith(self._generation_text):
                self.current_generation_content.moveCursor(QTextCursor.End)
                self.current_generation_content.insertPlainText(text[len(self._generation_text):])
            else:
                self.current_generation_content.setPlainText(text)
            self._generation_text = text
            cursor = self.current_generation_content.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.current_generation_content.setTextCursor(cursor)
            self.current_generation_status.setText(f"生成中：已接收 {len(text)} 个字符")

        def _on_manual_generation_update(self, kind: str, text: str) -> None:
            self._on_generation_update(kind, text)
            self.current_generation_content.viewport().repaint()
            self.current_generation_status.repaint()

        def _finish_generation_preview(self, kind: str, *, fallback: bool = False) -> None:
            if not getattr(self, "_generation_active", False) or kind != self._generation_kind:
                return
            path = (self.output_dir / ("report.md" if kind == "report" else "construction_plan.md")) if self.output_dir else None
            if path is not None and path.is_file():
                try:
                    self._generation_text = path.read_text(encoding="utf-8")
                    self.current_generation_content.setPlainText(self._generation_text)
                except OSError:
                    pass
            self.current_generation_status.setText("已完成（本地降级）" if fallback else "已完成")
            self._generation_active = False

        def _reload_generation_preview(self, kind: str, status_text: str) -> None:
            label, filename = {
                "report": ("损伤分析报告", "report.md"),
                "construction_plan": ("修复施工方案", "construction_plan.md"),
            }.get(kind, ("AI生成内容", "—"))
            self.current_file_stack.setCurrentIndex(1)
            self.current_file_title.setText(label)
            self.current_generation_file.setText(filename)
            path = self.output_dir / filename if self.output_dir is not None else None
            if path is not None and path.is_file():
                try:
                    self._generation_text = path.read_text(encoding="utf-8")
                    self.current_generation_content.setPlainText(self._generation_text)
                    cursor = self.current_generation_content.textCursor()
                    cursor.movePosition(QTextCursor.Start)
                    self.current_generation_content.setTextCursor(cursor)
                except OSError:
                    pass
            self.current_generation_status.setText(status_text)
            self._generation_active = False

        def export_findings(self) -> None:
            if self.output_dir is None or self.findings_table.rowCount() == 0:
                self._log_event("检测结果暂无可导出的记录")
                return
            output = self.output_dir / "findings.csv"
            lines = ["编号,文件名,损伤类型,置信度,损伤等级,状态"]
            for row in range(self.findings_table.rowCount()):
                values = [self.findings_table.item(row, column).text() if self.findings_table.item(row, column) else "" for column in range(6)]
                lines.append(",".join(value.replace(",", "，") for value in values))
            output.write_text("\n".join(lines), encoding="utf-8-sig")
            self._log_event(f"检测结果已导出：{output.name}")

        def stop_rendering(self) -> None:
            self.cancel_recognition()

        def _build_recognition_page(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            controls = QFrame()
            form = QFormLayout(controls)
            self.folder_edit = QLineEdit()
            self.folder_edit.setReadOnly(True)
            choose = QPushButton("選擇損傷圖片資料夾")
            choose.clicked.connect(self.choose_folder)
            folder_row = QHBoxLayout()
            folder_row.addWidget(self.folder_edit, 1)
            folder_row.addWidget(choose)
            form.addRow("輸入資料夾", folder_row)
            self.model_edit = QLineEdit(str(DEFAULT_MODEL_PATH))
            self.model_edit.setReadOnly(True)
            form.addRow("識別模型", self.model_edit)
            self.start_button = QPushButton("開始損傷識別")
            self.start_button.clicked.connect(self.start_recognition)
            self.cancel_button = QPushButton("停止處理")
            self.cancel_button.setEnabled(False)
            self.cancel_button.clicked.connect(self.cancel_recognition)
            self.recognition_progress = QProgressBar()
            self.recognition_progress.setRange(0, 100)
            controls_row = QHBoxLayout()
            controls_row.addWidget(self.start_button)
            controls_row.addWidget(self.cancel_button)
            controls_row.addWidget(self.recognition_progress, 1)
            form.addRow("批次控制", controls_row)
            layout.addWidget(controls)
            splitter = QSplitter(Qt.Horizontal)
            self.image_list = QListWidget()
            self.image_list.currentTextChanged.connect(self.show_selected_overlay)
            splitter.addWidget(self.image_list)
            self.preview = QLabel("尚未執行損傷識別")
            self.preview.setAlignment(Qt.AlignCenter)
            self.preview.setMinimumSize(560, 420)
            splitter.addWidget(self.preview)
            self.findings_table = ProportionalTableWidget(DETECTION_RESULT_COLUMN_PROPORTIONS)
            self.findings_table.setHorizontalHeaderLabels(["編號", "文件名", "損傷類型", "置信度", "損傷等級", "識別狀態"])
            findings_header = self.findings_table.horizontalHeader()
            findings_header.setDefaultAlignment(Qt.AlignCenter)
            self.findings_table.verticalHeader().setVisible(False)
            self.findings_table.horizontalHeaderItem(4).setToolTip(SCREENING_SEVERITY_TOOLTIP)
            splitter.addWidget(self.findings_table)
            splitter.setSizes([220, 620, 620])
            layout.addWidget(splitter, 1)
            return page

        def _build_report_page(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            self.report_title = QLabel("損傷分析報告")
            self.report_title.setObjectName("pageTitle")
            layout.addWidget(self.report_title)
            self.report_text = QLabel("完成損傷識別後，按下按鈕生成整批圖片的分析報告。")
            self.report_text.setWordWrap(True)
            layout.addWidget(self.report_text)
            self.generate_report_button = QPushButton("生成損傷分析報告")
            self.generate_report_button.clicked.connect(self.generate_report)
            layout.addWidget(self.generate_report_button, 0, Qt.AlignLeft)
            self.report_path_label = QLabel("報告尚未生成")
            layout.addWidget(self.report_path_label)
            layout.addStretch(1)
            return page

        def _build_plan_page(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            title = QLabel("修復施工方案")
            title.setObjectName("pageTitle")
            layout.addWidget(title)
            self.plan_table = QTableWidget(0, 5)
            self.plan_table.setHorizontalHeaderLabels(["圖片", "損傷類型", "修復方法", "數量依據", "需複核"])
            layout.addWidget(self.plan_table, 1)
            bottom = QHBoxLayout()
            self.plan_status_label = QLabel("施工方案尚未建立")
            self.build_plan_button = QPushButton("建立修復施工方案")
            self.build_plan_button.clicked.connect(self.build_plan)
            bottom.addWidget(self.build_plan_button)
            bottom.addWidget(self.plan_status_label, 1)
            layout.addLayout(bottom)
            return page

        def _build_render_page(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            title = QLabel("修復後渲染圖")
            title.setObjectName("pageTitle")
            layout.addWidget(title)
            self.render_hint = QLabel("完成修復施工方案後，可為每張原始損傷圖片生成修復後視覺化渲染圖。")
            self.render_hint.setWordWrap(True)
            layout.addWidget(self.render_hint)
            self.render_button = QPushButton("開始生成修復後渲染圖")
            self.render_button.clicked.connect(self.render_repairs)
            layout.addWidget(self.render_button, 0, Qt.AlignLeft)
            self.gallery = QListWidget()
            layout.addWidget(self.gallery, 1)
            return page

        def _apply_theme(self, scale: float | None = None) -> None:
            scale = float(scale if scale is not None else (self._layout_scale or 1.0))
            px = lambda value, minimum=1: self._scaled_metric(value, scale, minimum)
            self.setStyleSheet(f"""
                QWidget {{ background:#050e18; color:#dcecf5; font-family:'Noto Sans SC','Microsoft YaHei UI','Microsoft YaHei','SimHei'; font-size:{px(14, 9)}px; }}
                QMainWindow {{ background:#050e18; }}
                QDialog {{ background:#07101a; }}
                #brand {{ color:#f0f8ff; font-size:{px(28, 17)}px; font-weight:700; letter-spacing:0px; }}
                #systemStatus {{ color:#5be0a6; background:#071a20; border:1px solid #1d6561; padding:{px(9, 5)}px {px(14, 8)}px; border-radius:{px(5, 3)}px; }}
                #hudPanel {{ background:#0b1824; border:1px solid #27485f; border-radius:{px(6, 3)}px; }}
                #sourceCard {{ background:#0d1c2a; border:1px solid #27485f; border-radius:{px(5, 3)}px; }}
                #projectOverviewPanel {{ background:#0b1b2a; border:1px solid #315a73; border-radius:{px(6, 3)}px; }}
                #actionsPanel {{ background:#0b1824; border:1px solid #27485f; border-radius:{px(6, 3)}px; }}
                #panelTitle {{ color:#e6f4ff; font-size:{px(17, 11)}px; font-weight:700; }}
                #projectOverviewTitle {{ color:#d8eaf5; font-size:{px(14, 10)}px; font-weight:700; }}
                #accentText, #liveText {{ color:#24d5ee; font-weight:700; }}
                #versionText {{ color:#8aa8b8; font-size:{px(13, 9)}px; padding:0 {px(6, 3)}px; }}
                #overviewCounter {{ color:#8aa8b8; font-size:{px(13, 9)}px; }}
                #overviewCounter[overLimit="true"] {{ color:#ff9e2f; font-weight:700; }}
                #previewToolButton {{ padding:{px(6, 3)}px {px(9, 5)}px; min-height:{px(30, 20)}px; font-size:{px(12, 9)}px; }}
                #compareToggle {{ color:#a9bfcc; padding:{px(4, 2)}px {px(8, 4)}px; }}
                #resultFilter {{ background:#0d2638; border:1px solid #35647f; border-radius:{px(4, 2)}px; padding:{px(6, 3)}px {px(10, 5)}px; min-height:{px(30, 20)}px; color:#dcecf5; }}
                #riskText {{ color:#ff9e2f; }}
                #stageStatus {{ color:#25d7ef; font-size:{px(17, 11)}px; font-weight:700; }}
                #currentFile {{ color:#42c9ff; font-size:{px(19, 12)}px; font-weight:700; }}
                #metricsText {{ color:#b9d5df; }}
                #mutedText, #status {{ color:#8aa8b8; }}
                QPushButton {{ background:#10283a; border:1px solid #35647f; border-radius:{px(5, 3)}px; padding:{px(11, 5)}px {px(16, 8)}px; color:#e6f8ff; }}
                QPushButton:hover {{ border-color:#36d9ef; background:#153c51; }}
                QPushButton:disabled {{ color:#5f7783; background:#0a1823; }}
                #compactButton {{ padding:{px(4, 2)}px; min-height:{px(32, 22)}px; font-size:{px(13, 9)}px; }}
                #sourceToolButton {{ padding:{px(6, 3)}px; min-height:{px(32, 22)}px; font-size:{px(12, 9)}px; }}
                #startButton {{ background:#0d3044; border:2px solid #2de1f4; color:#ffffff; font-size:{px(17, 11)}px; font-weight:700; min-height:{px(36, 24)}px; padding:0 {px(12, 6)}px; }}
                #startButton:hover {{ background:#124b63; }}
                #stopButton {{ background:#2a190c; border:2px solid #ff9e2f; color:#ffb25a; font-size:{px(17, 11)}px; font-weight:700; min-height:{px(36, 24)}px; padding:0 {px(12, 6)}px; }}
                #stopButton:hover {{ background:#4a2b10; }}
                QCheckBox {{ color:#dcecf5; spacing:{px(8, 4)}px; }}
                #renderToggle {{ background:#101f2c; border:2px solid #ff9e2f; border-radius:{px(5, 3)}px; padding:{px(8, 4)}px {px(14, 7)}px; color:#ffd39b; font-size:{px(16, 10)}px; font-weight:700; }}
                #renderToggle:hover {{ background:#1d3040; border-color:#ffc36b; }}
                #renderToggle:checked {{ background:#4a2b10; color:#fff1d7; }}
                #renderToggle::indicator {{ width:{px(22, 13)}px; height:{px(22, 13)}px; border:2px solid #ff9e2f; border-radius:{px(4, 2)}px; background:#071a2a; margin-right:{px(8, 4)}px; }}
                #renderToggle::indicator:checked {{ background:#ff9e2f; image:none; }}
                QLineEdit, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit {{ background:#07131f; border:1px solid #27485f; border-radius:{px(4, 2)}px; padding:{px(7, 3)}px; }}
                QListWidget#settingsNav {{ background:#081522; border:1px solid #1f4057; border-radius:{px(6, 3)}px; padding:{px(10, 5)}px {px(8, 4)}px; }}
                QListWidget#settingsNav::item {{ color:#a9bfcc; padding:{px(14, 7)}px; margin:{px(3, 1)}px 0; border-radius:{px(5, 3)}px; }}
                QListWidget#settingsNav::item:hover {{ background:#102e43; color:#e9f8ff; }}
                QListWidget#settingsNav::item:selected {{ background:#174b66; color:#ffffff; border-left:3px solid #2de1f4; padding-left:{px(11, 6)}px; }}
                QTreeWidget::item {{ padding:{px(8, 4)}px {px(4, 2)}px; }}
                QTreeWidget::item:selected {{ background:#164b66; color:#ffffff; }}
                QSplitter::handle {{ background:#1b3c50; }}
                QTabWidget::pane {{ background:#050e18; border:1px solid #164765; top:-1px; }}
                QTabBar::tab {{ background:#071a2a; color:#dcecf5; border:1px solid #164765; padding:{px(10, 5)}px {px(18, 9)}px; min-width:{px(112, 68)}px; }}
                QTabBar::tab:selected {{ background:#dcecf5; color:#071a2a; border-color:#dcecf5; font-weight:700; }}
                QTabBar::tab:hover:!selected {{ background:#0d3448; color:#ffffff; }}
                #settingsPageTitle {{ color:#f0f8ff; font-size:{px(23, 15)}px; font-weight:700; padding:{px(4, 2)}px 0 {px(8, 4)}px 0; }}
                #kbBreadcrumb {{ color:#a9bfcc; font-size:{px(15, 10)}px; padding:{px(2, 1)}px 0; }}
                #knowledgeFileList {{ alternate-background-color:#0a1926; }}
                QDialogButtonBox QPushButton {{ min-width:{px(96, 58)}px; }}
                #settingsSaveButton {{ background:#4a2b10; border:1px solid #ff9e2f; color:#ffd39b; font-weight:700; }}
                QHeaderView::section {{ background:#0d2d42; color:#d8f5ff; padding:{px(8, 4)}px; border:0; }}
                QTableWidget::item:selected, QListWidget::item:selected {{ background:#0b536c; color:#ffffff; }}
                QProgressBar {{ background:#04111d; border:1px solid #1a4e67; height:{px(18, 11)}px; text-align:center; color:#d8f7ff; }}
                QProgressBar::chunk {{ background:#20c8e0; }}
                #status {{ padding:{px(6, 3)}px {px(4, 2)}px; }}
                #gpuStatus {{ color:#16d38b; font-weight:700; }}
                #progressRing {{ background:#07131f; border:1px solid #1a4e67; border-radius:{px(60, 36)}px; }}
            """)

        def _sync_primary_action_heights(self) -> None:
            """Keep the primary action and render toggle aligned after repolish."""
            height = max(
                self._scaled_metric(48, self._layout_scale or 1.0, 32),
                self.start_button.sizeHint().height(),
                self.render_checkbox.sizeHint().height(),
            )
            for widget_name in ("start_button", "render_checkbox"):
                widget = getattr(self, widget_name, None)
                if widget is not None:
                    widget.setMinimumHeight(height)
                    widget.setFixedHeight(height)

        def _set_primary_button_running(self, *, enabled: bool) -> None:
            """Show the workflow as active without changing stage cancellation support."""
            self.start_button.setEnabled(enabled)
            self.start_button.setText("停止")
            self.start_button.setObjectName("stopButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaStop))
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._sync_primary_action_heights()

        def _set_primary_button_idle(self) -> None:
            """Restore the primary action only after the workflow reaches a terminal state."""
            self.start_button.setEnabled(True)
            self.start_button.setText("启动")
            self.start_button.setObjectName("startButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaPlay))
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._sync_primary_action_heights()

        def select_stage(self, index: int) -> None:
            if index > self.stage_index:
                self.status_label.setText(f"請先完成「{STAGES[self.stage_index]}」階段。")
                return
            self.stage_index = index
            if hasattr(self, "content"):
                self.content.setCurrentIndex(index)
            for idx, button in enumerate(getattr(self, "stage_buttons", [])):
                button.setChecked(idx == index)
                button.setEnabled(idx <= self.stage_index)
            if hasattr(self, "status_label"):
                self.status_label.setText(STAGE_HELP[index])

        def choose_folder(self) -> None:
            folder = QFileDialog.getExistingDirectory(self, "選擇損傷圖片資料夾")
            if not folder:
                return
            path = Path(folder)
            images = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}]
            if not images:
                QMessageBox.warning(self, "資料夾無圖片", "所選資料夾沒有支援的損傷圖片。")
                return
            self.folder = path
            self.selected_image_paths = sorted(images, key=lambda image: image.name.casefold())
            self.folder_edit.setText(str(path))
            self.image_list.clear()
            self.image_list.addItems([p.name for p in self.selected_image_paths])
            self.source_count.setText(f"共 {len(images)} 个文件")
            self.live_results.clear()
            self._refresh_hud_metrics(processing=False)
            self.stage_index = 0
            self.select_stage(0)
            self.status_label.setText(f"已載入 {len(images)} 張損傷圖片，準備開始識別。")

        def choose_files(self) -> None:
            paths, _ = QFileDialog.getOpenFileNames(self, "添加损伤图片", "", "图片文件 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff)")
            if not paths:
                return
            selected = sorted((Path(path) for path in paths), key=lambda path: path.name.casefold())
            self._append_image_paths(selected)
            self.live_results.clear()
            self._refresh_hud_metrics(processing=False)
            self.stage_index = 0
            self.select_stage(0)
            self.status_label.setText(f"已添加 {len(selected)} 张损伤图片，准备开始识别。")

        def remove_selected_images(self) -> None:
            row = self.image_list.currentRow()
            if row < 0:
                return
            self.image_list.removeRow(row)
            if self.selected_image_paths is None and self.folder is not None:
                self.selected_image_paths = [self.folder / self.image_list.item(index).text() for index in range(self.image_list.rowCount())]
            elif self.selected_image_paths is not None and row < len(self.selected_image_paths):
                self.selected_image_paths.pop(row)
            self.source_count.setText(f"共 {self.image_list.count()} 个文件")
            self._refresh_hud_metrics(processing=False)

        def refresh_image_queue(self) -> None:
            if self.selected_image_paths is not None:
                self.selected_image_paths = [path for path in self.selected_image_paths if path.is_file()]
                self.image_list.clear()
                self.image_list.addItems([path.name for path in self.selected_image_paths])
                self.source_count.setText(f"共 {len(self.selected_image_paths)} 个文件")
                return
            if self.folder is not None:
                folder = self.folder
                images = sorted((path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}), key=lambda path: path.name.casefold())
                self.image_list.clear()
                self.image_list.addItems([path.name for path in images])
                self.source_count.setText(f"共 {len(images)} 个文件")

        def start_recognition(self) -> None:
            if self.plan_worker is not None and self.plan_worker.isRunning():
                self.status_label.setText("施工方案仍在生成，完成後方可啟動新的識別流程。")
                return
            if self.folder is None:
                QMessageBox.warning(self, "尚未選擇資料夾", "請先選擇包含損傷圖片的資料夾。")
                return
            self.output_dir = self.folder / "構件視界_分析輸出"
            project_overview = self.project_overview_edit.toPlainText()
            if len(project_overview) > PROJECT_OVERVIEW_MAX_CHARS:
                QMessageBox.warning(self, "项目概括过长", f"项目概括不能超过 {PROJECT_OVERVIEW_MAX_CHARS} 字。")
                return
            self.workflow_stop_requested = False
            self.workflow_retry_pending = False
            self.auto_worker = None
            self.plan_worker = None
            self.render_worker = None
            self.summary = None
            self.current_report = None
            self.current_construction_plan = None
            self._review_dialog_scheduled = False
            self._construction_review_dialog_scheduled = False
            self._plan_generation_scheduled = False
            self.review_report_button.setText("审核分析报告")
            self.review_report_button.setEnabled(False)
            self.review_report_button.setVisible(False)
            self.review_construction_plan_button.setEnabled(False)
            self.review_construction_plan_button.setVisible(False)
            self.render_checkbox.setEnabled(True)
            self.live_results.clear()
            self.findings_table.setRowCount(0)
            self.preview.clear()
            self.preview.setText("正在準備損傷識別…")
            self.gpu_status.setText(_resolve_yolo_device()[1])
            self._set_workflow_stage(
                "損傷識別與分割",
                system_status="執行中",
                status_text="正在執行損傷識別與分割。",
                log_message="系統：開始自動處理",
            )
            self._refresh_hud_metrics(processing=True)
            self._hud_timer.start(800)
            self.worker = RecognitionWorker(self.folder, self.output_dir, self.model_edit.text().strip(), project_overview, self.selected_image_paths)
            self.worker.progress.connect(self._on_recognition_progress)
            self.worker.image_done.connect(self.on_image_done)
            self.worker.completed.connect(self.on_recognition_completed)
            self.worker.failed.connect(self.on_recognition_failed)
            self.start_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.start_button.setText("停止")
            self.start_button.setObjectName("stopButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaStop))
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._sync_primary_action_heights()
            if self.image_list.count():
                self.image_list.setCurrentRow(0)
                first_name = self.image_list.item(0).text()
                self.show_selected_overlay(first_name)
            self.worker.start()

        def toggle_workflow(self) -> None:
            if self.render_worker is not None and self.render_worker.isRunning():
                self.cancel_recognition()
                return
            if self.plan_worker is not None and self.plan_worker.isRunning():
                self.status_label.setText("施工方案仍在生成；此階段不提供取消，完成後可再次啟動。")
                return
            running = bool(
                (self.worker is not None and self.worker.isRunning())
                or (self.auto_worker is not None and self.auto_worker.isRunning())
            )
            if running:
                self.cancel_recognition()
            elif self.workflow_retry_pending and self.summary and self.output_dir:
                self.summary["project_overview"] = self.project_overview_edit.toPlainText()
                (self.output_dir / "batch_summary.json").write_text(json.dumps(self.summary, ensure_ascii=False, indent=2), encoding="utf-8")
                self.workflow_retry_pending = False
                self.workflow_stop_requested = False
                self._log_event("系统：复用已完成识别结果，重试报告及后续阶段")
                self._continue_automatic_workflow()
            else:
                self.start_recognition()

        def _on_recognition_progress(self, value: int, message: str) -> None:
            # Recognition occupies the first quarter of the end-to-end workflow.
            self.recognition_progress.setValue(int(max(0, min(100, value)) * 0.25))
            self.status_label.setText(message)
            self._refresh_hud_metrics(processing=True)

        def cancel_recognition(self) -> None:
            self.workflow_stop_requested = True
            if self.render_worker is not None and self.render_worker.isRunning():
                self.render_worker.stop()
                self._set_primary_button_running(enabled=False)
                self.system_status.setText("● 系統狀態：正在停止渲染")
                self.stage_status.setText("處理狀態\n正在停止修复渲染")
                self.status_label.setText("正在等待当前渲染项结束，后续渲染项将取消。")
                self._log_event("系統：收到停止剩餘修复渲染指令")
                return
            if self.worker is not None:
                self.worker.stop()
            if self.auto_worker is not None:
                self.auto_worker.stop()
            self.cancel_button.setEnabled(False)
            self.start_button.setText("启动")
            self.start_button.setObjectName("startButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaPlay))
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._sync_primary_action_heights()
            self.system_status.setText("● 系統狀態：正在停止")
            self.status_label.setText("正在停止，會保留已完成的圖片結果。")
            self._log_event("系統：收到停止當前流程指令")

        def on_image_done(self, name: str, result: dict[str, Any]) -> None:
            self.live_results[name] = result
            self._show_preview_for_result(result)
            done = len(self.live_results)
            total = max(self.image_list.count(), done)
            self._refresh_hud_metrics(processing=done < total)
            self._update_current_file_details(name, result)
            self.preview_detail.setText(f"FRAME {done:04d} / {total:04d}   •   {result.get('status', '').upper()}")
            self._log_event(f"完成：{name}  [{result.get('status', 'unknown')}]")
            status_map = {"success": "已完成", "no_detection": "未检出", "failed": "异常"}
            self.image_list.update_task(name, status=status_map.get(str(result.get("status", "")), "已完成"))
            findings = result.get("damage_findings", []) or []
            if not findings:
                row = self.findings_table.rowCount()
                self.findings_table.insertRow(row)
                status = result.get("status", "")
                label = "處理失敗" if status == "failed" else "未檢出"
                detail = result.get("error") if status == "failed" else status
                values = [str(row + 1), name, label, "—", "—", detail or status]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if col in {1, 2}:
                        item.setToolTip(str(value))
                    self.findings_table.setItem(row, col, item)
                self._refresh_result_filter_options()
                return
            for finding in findings:
                row = self.findings_table.rowCount()
                self.findings_table.insertRow(row)
                screening = finding.get("screening_severity")
                screening_level = (
                    screening.get("level") if isinstance(screening, dict) else None
                )
                values = [
                    str(row + 1),
                    name,
                    finding.get("class_name", ""),
                    f"{float(finding.get('score', 0))*100:.1f}%",
                    _screening_severity_label(screening_level),
                    status_map.get(
                        str(result.get("status", "")), str(result.get("status", ""))
                    ),
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if col in {1, 2}:
                        item.setToolTip(str(value))
                    elif col == 4:
                        item.setToolTip(SCREENING_SEVERITY_TOOLTIP)
                    self.findings_table.setItem(row, col, item)
            self._refresh_result_filter_options()

        def on_recognition_failed(self, message: str) -> None:
            self.start_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.start_button.setText("启动")
            self.start_button.setObjectName("startButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaPlay))
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._sync_primary_action_heights()
            self.status_label.setText(message)
            self.system_status.setText("● 系統狀態：失敗")
            self.stage_status.setText("處理狀態\n損傷識別失敗，流程已停止")
            self._hud_timer.stop()
            QMessageBox.critical(self, "損傷識別失敗", message)

        def on_recognition_completed(self, payload: dict[str, Any]) -> None:
            self.summary = payload["summary"]
            self.gpu_status.setText(str(self.summary.get("device_status", _resolve_yolo_device()[1])))
            self.start_button.setEnabled(True)
            self.cancel_button.setEnabled(True)
            self.start_button.setText("停止")
            self.start_button.setObjectName("stopButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaStop))
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._sync_primary_action_heights()
            self.stage_index = 1
            self._refresh_hud_metrics(processing=False)
            self.stage_status.setText("處理狀態\n損傷識別完成，準備生成報告")
            self.system_status.setText("● 系統狀態：階段切換中")
            self._hud_timer.stop()
            self.status_label.setText("損傷識別完成，已解鎖後續分析流程。")
            self._log_event("階段完成：損傷識別")
            QTimer.singleShot(0, self._continue_automatic_workflow)

        def _continue_automatic_workflow(self) -> None:
            if not self.summary or not self.output_dir:
                return
            if self.workflow_stop_requested:
                self.on_workflow_cancelled("已停止當前流程，未開始後續階段。")
                return
            self.auto_worker = AutomaticWorkflowWorker(
                self.summary,
                self.output_dir,
                self.api_config,
                self.render_checkbox.isChecked(),
                self.settings,
            )
            self.auto_worker.stage.connect(self.on_auto_stage)
            self.auto_worker.progress.connect(self.on_auto_progress)
            self.auto_worker.generation_update.connect(self._on_generation_update)
            self.auto_worker.report_done.connect(self.on_auto_report_done)
            self.auto_worker.plan_done.connect(self.on_auto_plan_done)
            self.auto_worker.construction_plan_done.connect(self.on_auto_construction_plan_done)
            self.auto_worker.render_item.connect(self.on_auto_render_item)
            self.auto_worker.render_done.connect(self.on_auto_render_done)
            self.auto_worker.completed.connect(self.on_auto_completed)
            self.auto_worker.cancelled.connect(self.on_workflow_cancelled)
            self.auto_worker.failed.connect(self.on_workflow_failed)
            self.auto_worker.start()

        def on_auto_stage(self, stage: str, system_status: str, progress: int) -> None:
            if stage == "生成損傷分析報告":
                self._begin_generation_preview("report")
            elif stage == "建立修復施工方案":
                self._begin_generation_preview("construction_plan")
            self._set_workflow_stage(
                stage,
                system_status=system_status,
                status_text=f"正在執行：{stage}。",
                log_message=f"階段開始：{stage}",
                overall_progress=progress,
            )
            if stage == "修復後渲染圖生成":
                self.render_results = []
                self.gallery.clear()
                self.cancel_button.setEnabled(True)

        def on_auto_progress(self, value: int, message: str) -> None:
            self.recognition_progress.setValue(value)
            self.status_label.setText(message)
            self._log_event(message)

        def _set_report_for_review(self, report: Any) -> None:
            from runtime.damage_report_schema import DamageReport

            validated = report if isinstance(report, DamageReport) else DamageReport.model_validate(report)
            self.current_report = validated
            is_confirmed = validated.review_status == "confirmed_by_human"
            self.review_report_button.setText(
                "重新生成施工方案" if is_confirmed else "审核分析报告"
            )
            self.review_report_button.setEnabled(True)
            self.review_report_button.setVisible(not is_confirmed)

        def _schedule_report_review_dialog(self) -> None:
            if self._review_dialog_open or self._review_dialog_scheduled:
                return
            self._review_dialog_scheduled = True

            def open_scheduled_dialog() -> None:
                self._review_dialog_scheduled = False
                if self.current_report is not None:
                    self.open_report_review_dialog()

            QTimer.singleShot(0, open_scheduled_dialog)

        def handle_report_action(self) -> None:
            if self.current_report is None:
                QMessageBox.information(self, "暂无分析报告", "请先生成损伤分析报告。")
                return
            if self.current_report.review_status == "confirmed_by_human":
                self.build_plan()
                return
            self.open_report_review_dialog()

        def open_report_review_dialog(self) -> None:
            if self._review_dialog_open:
                return
            if self.current_report is None or self.output_dir is None:
                QMessageBox.warning(self, "报告尚未生成", "当前没有可审核的损伤分析报告。")
                return
            report_path = self.output_dir / "report.json"
            if not report_path.is_file():
                QMessageBox.warning(self, "报告尚未生成", f"找不到报告文件：{report_path}")
                return

            self._review_dialog_open = True
            dialog = DamageReportReviewDialog(self.current_report, report_path, self)
            try:
                result = dialog.exec()
            finally:
                self._review_dialog_open = False

            if result != QDialog.Accepted or dialog.confirmed_report is None:
                self.current_report = dialog.current_report
                self.review_report_button.setText("审核分析报告")
                self.review_report_button.setEnabled(True)
                self.review_report_button.setVisible(True)
                self.system_status.setText("● 系統狀態：等待人工复核")
                self.stage_status.setText("處理狀態\n报告待人工确认")
                self.status_label.setText("损伤报告仍待人工审核；确认前不会生成施工方案。")
                self._log_event("人工复核窗口已关闭，报告保持待确认状态")
                return

            report = dialog.confirmed_report
            self.current_report = report
            self.review_report_button.setEnabled(False)
            self.review_report_button.setVisible(False)
            self._set_generated_markdown_paths()
            self._render_profile_docx("損傷分析報告", report.model_dump(mode="json"))
            self._log_event(f"损伤报告已由 {report.human_review.reviewer} 人工确认")
            self.status_label.setText("损伤报告已确认，正在生成施工方案。")
            self._set_primary_button_running(enabled=False)
            if not self._plan_generation_scheduled:
                self._plan_generation_scheduled = True
                QTimer.singleShot(0, self.build_plan)

        def on_auto_report_done(self, report: Any) -> None:
            self.report_text.setText(report.executive_summary)
            self.report_path_label.setText(f"報告已生成：{self.output_dir / 'report.md'}")
            self._set_report_for_review(report)
            self._set_generated_markdown_paths()
            self._log_event("階段完成：損傷分析報告")
            self._finish_generation_preview("report")
            self._schedule_report_review_dialog()

        def on_auto_plan_done(self, plan_summary: dict[str, Any]) -> None:
            self.plan_summary = plan_summary
            self.plan_table.setRowCount(0)
            for line in plan_summary.get("lines", []):
                row = self.plan_table.rowCount()
                self.plan_table.insertRow(row)
                values = [line["image_name"], line["class_name"], line["repair_method"], line["quantity_basis"], "是" if line["review_required"] else "否"]
                for col, value in enumerate(values):
                    self.plan_table.setItem(row, col, QTableWidgetItem(str(value)))
            self.plan_status_label.setText(f"基础修复工法已建立：{plan_summary['finding_count']} 项损伤")

        def on_auto_construction_plan_done(
            self, construction_plan: Any, *, fallback: bool = False
        ) -> None:
            count = len(getattr(construction_plan, "work_items", []) or [])
            plan_status = str(getattr(construction_plan, "plan_status", "pending_engineer_review"))
            status_text = {
                "pending_engineer_review": "待工程師審核，尚未施工放行",
                "hold": "已暫停，須完成專項評估",
                "evidence_inconsistent": "證據不一致，禁止施工",
            }.get(plan_status, f"狀態：{plan_status}")
            self.plan_status_label.setText(f"施工方案草案：{count} 項損傷，{status_text}")
            self._set_generated_markdown_paths()
            self._finish_generation_preview("construction_plan", fallback=fallback)

        def on_plan_worker_progress(self, value: int, message: str) -> None:
            self.recognition_progress.setValue(value)
            self.status_label.setText(message)
            self.current_generation_status.setText(message)

        def _set_construction_plan_for_review(self, construction_plan: Any) -> None:
            from runtime.construction_plan_schema import ConstructionPlan

            validated = (
                construction_plan
                if isinstance(construction_plan, ConstructionPlan)
                else ConstructionPlan.model_validate(construction_plan)
            )
            self.current_construction_plan = validated
            pending = validated.review_status != "confirmed_by_engineer"
            self.review_construction_plan_button.setText("审核施工方案")
            self.review_construction_plan_button.setEnabled(pending)
            self.review_construction_plan_button.setVisible(pending)
            self.render_checkbox.setEnabled(self._construction_render_allowed())

        def _schedule_construction_plan_review_dialog(self) -> None:
            if (
                self._construction_review_dialog_open
                or self._construction_review_dialog_scheduled
            ):
                return
            self._construction_review_dialog_scheduled = True

            def open_scheduled_dialog() -> None:
                self._construction_review_dialog_scheduled = False
                if self.current_construction_plan is not None:
                    self.open_construction_plan_review_dialog()

            QTimer.singleShot(0, open_scheduled_dialog)

        def _construction_render_allowed(self) -> bool:
            plan = self.current_construction_plan
            return bool(
                plan is not None
                and getattr(plan, "review_status", "") == "confirmed_by_engineer"
                and getattr(plan, "plan_status", "") in {
                    "pending_engineer_review", "hold", "evidence_inconsistent"
                }
                and getattr(plan, "construction_released", True) is False
            )

        @staticmethod
        def _construction_plan_has_local_fallback(plan: Any) -> bool:
            return any(
                getattr(item, "repair_method_source", "") == "local_fallback"
                for item in getattr(plan, "work_items", [])
            )

        def _on_render_option_toggled(self, checked: bool) -> None:
            if checked and self._construction_render_allowed():
                QTimer.singleShot(0, self.render_repairs)

        def open_construction_plan_review_dialog(self) -> None:
            if self._construction_review_dialog_open:
                return
            if self.current_construction_plan is None or self.output_dir is None:
                QMessageBox.warning(self, "施工方案尚未生成", "当前没有可审核的施工方案。")
                return
            plan_path = self.output_dir / "construction_plan.json"
            if not plan_path.is_file():
                QMessageBox.warning(self, "施工方案尚未生成", f"找不到施工方案文件：{plan_path}")
                return

            self._construction_review_dialog_open = True
            dialog = ConstructionPlanReviewDialog(
                self.current_construction_plan, plan_path, self
            )
            try:
                result = dialog.exec()
            finally:
                self._construction_review_dialog_open = False

            if result != QDialog.Accepted or dialog.confirmed_plan is None:
                self.current_construction_plan = dialog.current_plan
                self._reload_generation_preview(
                    "construction_plan", "审核尚未确认；方案保持待审核"
                )
                self.review_construction_plan_button.setText("审核施工方案")
                self.review_construction_plan_button.setEnabled(True)
                self.review_construction_plan_button.setVisible(True)
                self.render_checkbox.setEnabled(False)
                self.system_status.setText("● 系統狀態：等待施工方案审核")
                self.stage_status.setText("處理狀態\n施工方案待工程師确认")
                self.status_label.setText(
                    "施工方案仍待人工审核；确认前不会生成修复渲染图。"
                )
                self._set_primary_button_running(enabled=False)
                self._log_event("施工方案审核窗口已关闭，方案保持待确认状态")
                return

            plan = dialog.confirmed_plan
            self.current_construction_plan = plan
            self.review_construction_plan_button.setEnabled(False)
            self.review_construction_plan_button.setVisible(False)
            self._set_generated_markdown_paths()
            self._log_event(f"施工方案已由 {plan.human_review.reviewer} 人工确认")
            if self._construction_render_allowed():
                self.render_checkbox.setEnabled(True)
                hold_preview = str(plan.plan_status) == "hold"
                fallback_preview = self._construction_plan_has_local_fallback(plan)
                evidence_warning = str(plan.plan_status) == "evidence_inconsistent"
                self.plan_status_label.setText(
                    f"施工方案已由 {plan.human_review.reviewer} 完成人工审核；"
                    + (
                        "证据身份存在不一致，本次仅生成待复核预览"
                        if evidence_warning
                        else (
                        "远端草稿未通过校验，本次仅生成本地保守预览"
                        if fallback_preview
                        else ("方案仍保持暂停，仅允许生成非施工放行预览" if hold_preview else "尚未施工放行")
                        )
                    )
                )
                self.system_status.setText("● 系統狀態：施工方案已审核")
                self.stage_status.setText("處理狀態\n施工方案审核完成")
                if evidence_warning:
                    review_notice = (
                        "施工方案已完成人工审核；证据身份存在不一致，当前仅生成待复核预览，"
                        "预览不代表施工放行。"
                    )
                elif fallback_preview:
                    review_notice = (
                        "施工方案已完成人工审核；远端草稿未通过校验，当前仅生成本地保守预览，"
                        "预览不代表施工放行。"
                    )
                elif hold_preview:
                    review_notice = (
                        "施工方案已完成人工审核但仍保持暂停；可生成修复预览图，"
                        "预览不代表施工放行。"
                    )
                else:
                    review_notice = (
                        "施工方案已完成人工审核，但尚未施工放行；修复后示意图现已可生成。"
                    )
                self.status_label.setText(
                    review_notice
                )
                if self.render_checkbox.isChecked():
                    self._reload_generation_preview(
                        "construction_plan", "审核完成；正在进入修复渲染阶段"
                    )
                    QTimer.singleShot(0, self.render_repairs)
                    return
                self.recognition_progress.setValue(100)
                self.system_status.setText("● 系統狀態：已完成")
                self.stage_status.setText("處理狀態\n流程完成")
                self.status_label.setText(
                    "施工方案已完成人工审核；未选择可选修复渲染，流程已结束，且尚未施工放行。"
                )
                self._reload_generation_preview(
                    "construction_plan", "审核完成；流程已结束"
                )
                self._log_event("流程完成：施工方案审核已结束，未生成可选修复渲染")
                self._set_primary_button_idle()
                return

            self.render_checkbox.setEnabled(False)
            disposition = str(plan.plan_status)
            fallback_blocked = self._construction_plan_has_local_fallback(plan)
            block_reason = (
                "施工方案是远端草稿校验失败后的本地回退内容，必须重新生成有效远端方案后才能渲染。"
                if fallback_blocked
                else f"施工方案已审核，但工程状态为 {disposition}，不可生成修复渲染；流程已结束，且尚未施工放行。"
            )
            self.plan_status_label.setText(
                (
                    "施工方案已审核；本地回退草稿保持阻断，尚未施工放行"
                    if fallback_blocked
                    else f"施工方案已审核；工程状态 {disposition} 保持阻断，尚未施工放行"
                )
            )
            self.system_status.setText("● 系統狀態：施工方案保持阻断")
            self.stage_status.setText("處理狀態\n审核完成，工程状态阻断")
            self.status_label.setText(block_reason)
            self.recognition_progress.setValue(100)
            self._reload_generation_preview(
                "construction_plan",
                f"审核完成；工程状态 {disposition} 阻断渲染，流程已结束",
            )
            self._log_event(
                f"流程完成：施工方案审核已结束；工程状态阻断（{disposition}）"
            )
            self._set_primary_button_idle()

        def on_plan_worker_succeeded(
            self,
            construction_plan: Any,
            answer_source_mode: str,
            fallback: bool,
        ) -> None:
            self.on_auto_construction_plan_done(construction_plan, fallback=fallback)
            self._set_construction_plan_for_review(construction_plan)
            self._plan_generation_scheduled = False
            self.review_report_button.setEnabled(False)
            self.review_report_button.setVisible(False)
            self.stage_index = 3
            self.select_stage(3)
            self.recognition_progress.setValue(75)
            plan_state = str(
                getattr(construction_plan, "plan_status", "pending_engineer_review")
            )
            mode = "本地保守降級" if fallback else "遠程草稿、本地組裝"
            self.status_label.setText(
                f"修復施工草案完成（{mode}；來源 {answer_source_mode}；狀態 {plan_state}；"
                "尚未施工放行），等待工程師人工审核。"
            )
            self.system_status.setText("● 系統狀態：等待施工方案审核")
            self.stage_status.setText("處理狀態\n施工草案待工程師審核")
            self._set_primary_button_running(enabled=False)
            self.render_checkbox.setEnabled(False)
            self._log_event("階段完成：修復施工方案")
            if fallback:
                self._log_event("施工方案已使用本地保守草案，仍须人工审核")
            self._log_event("流程暫停：等待施工方案人工審核")
            self._schedule_construction_plan_review_dialog()

        def on_plan_worker_failed(self, message: str) -> None:
            self._generation_active = False
            self._plan_generation_scheduled = False
            self.current_generation_status.setText("生成失敗，已保留確認報告，可重試")
            self.review_report_button.setText("重新生成施工方案")
            self.review_report_button.setEnabled(self.current_report is not None)
            self.review_report_button.setVisible(self.current_report is not None)
            self.review_construction_plan_button.setEnabled(False)
            self.review_construction_plan_button.setVisible(False)
            self.render_checkbox.setEnabled(True)
            self.system_status.setText("● 系統狀態：施工方案生成失敗")
            self.stage_status.setText("處理狀態\n施工方案可重試")
            self._set_primary_button_idle()
            self.status_label.setText(
                f"施工方案生成失敗；確認報告已保留，可直接重試。{message}"
            )
            self._log_event(f"施工方案失敗：{message}")
            QMessageBox.warning(self, "施工方案尚未生成", message)

        def on_plan_worker_finished(self) -> None:
            worker = self.sender()
            if worker is self.plan_worker:
                self.plan_worker = None

        def on_render_worker_completed(self, payload: dict[str, Any]) -> None:
            self.render_results = list(payload.get("results", []))
            self.recognition_progress.setValue(100)
            self.render_stop_button.setVisible(False)
            self.render_stop_button.setEnabled(False)
            status_counts = {
                status: sum(1 for result in self.render_results if getattr(result, "status", "") == status)
                for status in ("success", "resumed", "failed", "cancelled")
            }
            usable_count = status_counts["success"] + status_counts["resumed"]
            failed_count = status_counts["failed"]
            cancelled_count = status_counts["cancelled"]
            total_count = len(self.render_results)
            count_summary = (
                f"生成 {status_counts['success']} 张，沿用 {status_counts['resumed']} 张，"
                f"失败 {failed_count} 张，取消 {cancelled_count} 张"
            )
            self._log_event(f"修复渲染结果：{count_summary}")

            if total_count and failed_count == total_count:
                first_reason = next(
                    (str(getattr(result, "message", "")).strip() for result in self.render_results if getattr(result, "message", "")),
                    "未提供失败原因",
                )
                self.system_status.setText("● 系統狀態：修复渲染失败")
                self.stage_status.setText("處理狀態\n修复渲染失败")
                self.status_label.setText(
                    f"未生成任何修复渲染图；失败 {failed_count}/{total_count}。首个原因：{first_reason}"
                )
                self.current_generation_status.setText("修复渲染失败")
                self._log_event(f"阶段失败：修复渲染（{failed_count}/{total_count}）")
            elif failed_count or cancelled_count or usable_count != total_count:
                self.system_status.setText("● 系統狀態：部分完成")
                self.stage_status.setText("處理狀態\n修复渲染部分完成")
                self.status_label.setText(
                    f"修复渲染部分完成：成功 {usable_count} 张，失败 {failed_count} 张，取消 {cancelled_count} 张。"
                )
                self.current_generation_status.setText("修复渲染部分完成")
                self._log_event(f"流程部分完成：修复渲染（{count_summary}）")
            else:
                self.system_status.setText("● 系統狀態：已完成")
                self.stage_status.setText("處理狀態\n流程完成")
                self.status_label.setText(f"修复渲染图处理完成，已生成或沿用 {usable_count} 张。")
                self.current_generation_status.setText("修复渲染图处理完成")
                self._log_event("階段完成：修復後渲染圖")
                self._log_event("流程完成：全部阶段已结束")
            self._set_primary_button_idle()

        def on_render_worker_cancelled(self, payload: dict[str, Any]) -> None:
            self.render_results = list(payload.get("results", []))
            self.render_stop_button.setVisible(False)
            self.render_stop_button.setEnabled(False)
            self.system_status.setText("● 系統狀態：已停止")
            self.stage_status.setText("處理狀態\n修复渲染已停止")
            self.status_label.setText("修复渲染已停止；已完成结果保留，剩余项目已标记为取消。")
            self.current_generation_status.setText("修复渲染已停止")
            self._log_event("流程停止：修复渲染剩余项目已取消")
            self._set_primary_button_idle()

        def on_render_worker_failed(self, message: str) -> None:
            self.render_stop_button.setVisible(False)
            self.render_stop_button.setEnabled(False)
            self.system_status.setText("● 系統狀態：渲染失敗")
            self.stage_status.setText("處理狀態\n修复渲染失败")
            self.status_label.setText(f"修复渲染失败；已完成结果仍保留。{message}")
            self.current_generation_status.setText("修复渲染失败")
            self._log_event(f"修复渲染失败：{message}")
            self._set_primary_button_idle()

        def on_render_worker_finished(self) -> None:
            worker = self.sender()
            if worker is self.render_worker:
                self.render_worker = None

        def on_auto_render_item(self, result: Any) -> None:
            self.render_results.append(result)
            self.gallery.addItem(f"{Path(result.source_path).name}  →  {result.status}  →  {result.output_path or result.message}")
            if getattr(result, "status", "") == "failed":
                self._log_event(
                    f"修复渲染失败：{Path(result.source_path).name}：{getattr(result, 'message', '未知原因')}"
                )

        def on_auto_render_done(self, payload: dict[str, Any]) -> None:
            self.render_results = list(payload.get("results", []))
            self._log_event("階段完成：修復後渲染圖")

        def on_auto_progress_finished(self) -> None:
            self.recognition_progress.setValue(100)

        def on_auto_completed(self, payload: dict[str, Any]) -> None:
            self.cancel_button.setEnabled(False)
            self.render_stop_button.setVisible(False)
            self.render_stop_button.setEnabled(False)
            if payload.get("awaiting_human_review"):
                self._set_primary_button_running(enabled=False)
                self.system_status.setText("● 系統狀態：等待人工复核")
                self.stage_status.setText("處理狀態\n报告待人工确认")
                self.status_label.setText("损伤报告已生成；请复核 JSON、填写复核人并确认，确认前不会生成施工方案。")
                self.recognition_progress.setValue(50)
                self._log_event("流程暂停：等待损伤报告人工复核")
                return
            self._set_primary_button_idle()
            self.system_status.setText("● 系統狀態：已完成")
            self.stage_status.setText("處理狀態\n流程完成")
            self.status_label.setText("流程完成，所有已产生的报告、方案及渲染结果均已保存。")
            self.recognition_progress.setValue(100)
            self._log_event("流程完成：全部阶段已结束")

        def on_workflow_cancelled(self, message: str) -> None:
            if self._generation_active:
                self.current_generation_status.setText("生成已停止，保留已接收内容")
                self._generation_active = False
            self.cancel_button.setEnabled(False)
            self.start_button.setEnabled(True)
            self.start_button.setText("启动")
            self.start_button.setObjectName("startButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaPlay))
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._sync_primary_action_heights()
            self.render_stop_button.setVisible(False)
            self.render_stop_button.setEnabled(False)
            self.system_status.setText("● 系統狀態：已取消")
            self.stage_status.setText("處理狀態\n流程已取消")
            self.status_label.setText(message)
            self._log_event(f"流程取消：{message}")

        def on_workflow_failed(self, message: str) -> None:
            if self._generation_active:
                self.current_generation_status.setText("生成失败，保留已接收内容")
                self._generation_active = False
            self.cancel_button.setEnabled(False)
            self.start_button.setEnabled(True)
            self.start_button.setText("启动")
            self.start_button.setObjectName("startButton")
            self.start_button.setIcon(QApplication.style().standardIcon(QStyle.SP_MediaPlay))
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self._sync_primary_action_heights()
            self.render_stop_button.setVisible(False)
            self.render_stop_button.setEnabled(False)
            self.system_status.setText("● 系統狀態：失敗")
            self.stage_status.setText("處理狀態\n流程失敗")
            self.workflow_retry_pending = bool(self.summary and self.output_dir)
            formatted = _format_report_api_error(
                RuntimeError(message),
                base_url=self.api_config.get("responses_url", ""),
                model=self.api_config.get("responses_model", ""),
                api_key=self.api_config.get("responses_key", ""),
            ) if "Responses API" in message or "ReportGenerationError" in message else message
            if self.workflow_retry_pending:
                formatted = f"{formatted}\n\n再次点击“启动”将复用识别结果，只重试报告及后续阶段。"
            self.status_label.setText(formatted)
            self._log_event(formatted)
            QMessageBox.critical(self, "自動流程失敗", formatted)

        def show_selected_overlay(self, name: str) -> None:
            if not name:
                return
            item = self.live_results.get(name)
            if item is None and self.summary:
                item = next((r for r in self.summary.get("results", []) if r.get("image_name") == name), None)
            if item is None and self.folder is not None:
                item = {"image_path": str(self.folder / name)}
            if item is not None:
                self._update_current_file_details(name, item)
                self._show_preview_for_result(item)

        def _show_preview_for_result(self, item: dict[str, Any]) -> None:
            overlay = Path(str(item.get("overlay_path", "")))
            source = Path(str(item.get("image_path", "")))
            preview_path = overlay if overlay.is_file() else source
            if not preview_path.is_file():
                return
            pixmap = _load_pixmap_unicode(preview_path)
            if pixmap.isNull():
                self.preview.setText("預覽載入失敗")
                return
            self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            source_for_meta = source if source.is_file() else preview_path
            self.preview_metadata.setText(
                f"{source_for_meta.name}    {pixmap.width()} × {pixmap.height()}    "
                f"{source_for_meta.stat().st_size / (1024 * 1024):.1f} MB"
            )

        def generate_report(self) -> None:
            if not self.summary or not self.output_dir:
                return
            self._begin_generation_preview("report")
            self.current_file_stack.repaint()
            try:
                from runtime.responses_damage_report import (
                    ResponsesReportService,
                    build_local_fallback_report_from_summary,
                    normalize_responses_base_url,
                )
                snapshot_id, snapshot = self.settings.snapshot()
                profile = snapshot.generation_profiles["損傷分析報告"]
                kb_root = resolve_user_path(snapshot.knowledge_base.root_dir, default=user_knowledge_base_root())
                report_kb_error = ""
                try:
                    report_kb_result = _retrieve_profile_knowledge(
                        kb_root, profile, snapshot.knowledge_base, self.summary
                    )
                    chunks = list(report_kb_result.chunks)
                    if report_kb_result.used_scoped_fallback:
                        self._log_event("报告知识库词法未命中，已在損傷分析報告选定范围内使用代表片段")
                    if not report_kb_result.scope_available:
                        report_kb_error = "損傷分析報告 profile 未找到可用的已索引知识库片段，请先在设置中导入并选择文档"
                        self._log_event(f"{report_kb_error}；知识库不可用，将仅依据结构化证据继续调用 AI")
                except Exception as exc:
                    chunks = []
                    report_kb_result = KnowledgeBaseSearchResult((), scope_available=False)
                    report_kb_error = f"报告知识库检索失败：{type(exc).__name__}: {exc}"
                    self._log_event(report_kb_error)
                generation_context = build_generation_context(
                    profile,
                    self.summary,
                    settings_snapshot_id=snapshot_id,
                    retrieved_chunks=chunks,
                    retrieval_mode=report_kb_result.retrieval_mode,
                    anchors=report_kb_result.anchors,
                    context_groups=report_kb_result.context_groups,
                    retrieval_warnings=report_kb_result.warnings,
                    route_diagnostics=report_kb_result.route_diagnostics,
                    retrieval_query=build_profile_query(profile.name, self.summary),
                    web_search_provider=_generation_web_search_provider(self.api_config, profile.model),
                    knowledge_base_scope_document_ids=report_kb_result.scope_document_ids,
                    knowledge_base_status=("unavailable" if report_kb_error else "used"),
                )
                responses_key = self.api_config.get("responses_key", "").strip()
                try:
                    if not responses_key:
                        raise ValueError("未配置 Responses API Key")
                    service = ResponsesReportService(
                        api_key=responses_key,
                        base_url=normalize_responses_base_url(self.api_config["responses_url"]),
                        model=profile.model or self.api_config["responses_model"],
                        timeout=180.0,
                        retries=2,
                        on_text_update=lambda text: self._on_manual_generation_update("report", text),
                    )
                    report = service.generate_report_from_summary(
                        self.output_dir / "batch_summary.json",
                        output_dir=self.output_dir,
                        generation_context=generation_context,
                    )
                except Exception as exc:
                    fallback_reason = str(exc).replace(responses_key, "<redacted>") if responses_key else str(exc)
                    report = build_local_fallback_report_from_summary(
                        self.output_dir / "batch_summary.json",
                        output_dir=self.output_dir,
                        reason=fallback_reason,
                        generation_context=generation_context,
                    )
                    self._log_event("远程报告服务不可用，已生成本地证据报告")
                self.status_label.setText(
                    f"损伤分析报告完成，知识库参考 {len(chunks)} 段（{generation_context.knowledge_base_retrieval_mode}；来源 {generation_context.answer_source_mode}）。"
                    if chunks else "损伤分析报告已本地降级：所选知识库范围无可用片段。"
                )
                self.report_path_label.setText(f"報告已生成：{self.output_dir / 'report.md'}")
                self.report_text.setText(report.executive_summary)
                self._set_report_for_review(report)
                self._set_generated_markdown_paths()
                self._finish_generation_preview("report")
                self.stage_index = 2
                self.select_stage(2)
                reference_note = (
                    f"已使用知识库 {len(chunks)} 段（{generation_context.knowledge_base_retrieval_mode}；来源 {generation_context.answer_source_mode}）"
                    if chunks else f"知识库范围不可用，已明确降级为 {generation_context.answer_source_mode}"
                )
                self.status_label.setText(f"损伤分析报告完成（{reference_note}），等待人工复核确认。")
                self._schedule_report_review_dialog()
            except ImportError as exc:
                self._generation_active = False
                QMessageBox.warning(
                    self,
                    "報告尚未生成",
                    "目前 GUI 使用的 Python 環境尚未安裝 openai 套件。\n"
                    f"環境：{sys.executable}\n"
                    f"請在 VS Code 終端執行：\n{sys.executable} -m pip install openai==2.53.0\n\n"
                    f"詳細錯誤：{exc}",
                )
            except Exception as exc:
                self._generation_active = False
                self._set_workflow_stage(
                    "損傷分析報告失敗，等待修正設定",
                    system_status="分析報告失敗",
                    status_text="损伤分析报告生成失败，请修正设置后重试。",
                    log_message=f"報告失敗：{type(exc).__name__}",
                )
                QMessageBox.warning(
                    self,
                    "報告尚未生成",
                    _format_report_api_error(
                        exc,
                        base_url=normalize_responses_base_url(self.api_config.get("responses_url")),
                        model=self.api_config.get("responses_model", ""),
                        api_key=self.api_config.get("responses_key", ""),
                    ),
                )

        def build_plan(self) -> None:
            if not self.summary or not self.output_dir:
                self._plan_generation_scheduled = False
                return
            if self.plan_worker is not None:
                return
            self._plan_generation_scheduled = True
            self.current_construction_plan = None
            self.review_construction_plan_button.setEnabled(False)
            self.review_construction_plan_button.setVisible(False)
            self.render_checkbox.setEnabled(False)
            self.review_report_button.setEnabled(False)
            self.review_report_button.setVisible(False)
            self._begin_generation_preview("construction_plan")
            self._set_primary_button_running(enabled=False)
            self.cancel_button.setEnabled(False)
            self._set_workflow_stage(
                "建立修復施工方案",
                system_status="施工方案準備中",
                status_text="正在建立確定性工法並準備施工方案上下文。",
                log_message="階段開始：建立修復施工方案",
                overall_progress=55,
            )
            settings_snapshot = copy.deepcopy(self.settings)
            self.plan_worker = ConstructionPlanWorker(
                self.summary,
                self.output_dir,
                self.api_config,
                settings_snapshot,
            )
            self.plan_worker.stage.connect(self.on_auto_stage)
            self.plan_worker.progress.connect(self.on_plan_worker_progress)
            self.plan_worker.generation_update.connect(self._on_generation_update)
            self.plan_worker.plan_done.connect(self.on_auto_plan_done)
            self.plan_worker.construction_plan_done.connect(self.on_plan_worker_succeeded)
            self.plan_worker.failed.connect(self.on_plan_worker_failed)
            self.plan_worker.finished.connect(self.on_plan_worker_finished)
            self.plan_worker.start()

        def render_repairs(self) -> None:
            if self.render_worker is not None and self.render_worker.isRunning():
                return
            if not self.summary or not self.output_dir or not self.plan_summary:
                return
            if not self._construction_render_allowed():
                plan_status = str(
                    getattr(self.current_construction_plan, "plan_status", "待审核")
                )
                message = (
                    f"施工方案尚未通过可渲染审核（当前工程状态：{plan_status}），"
                    "不可生成修复渲染。"
                )
                self.status_label.setText(message)
                self.current_generation_status.setText(message)
                self._set_primary_button_idle()
                return
            self.render_stop_button.setVisible(False)
            self._set_workflow_stage(
                "修復後渲染圖生成",
                system_status="渲染中",
                status_text=(
                    "正在生成非施工放行的修复预览图；施工方案仍保持暂停。"
                    if getattr(self.current_construction_plan, "plan_status", "") == "hold"
                    else "正在生成修復後渲染圖。"
                ),
                log_message="階段開始：修復後渲染圖",
                overall_progress=75,
            )
            reviewed_plan_lines = [
                {
                    "image_name": item.image_name,
                    "method_id": item.method_id,
                    "method_display_name": item.ai_method_name or item.method_display_name,
                    "repair_method": item.base_repair_method,
                    "original_image_path": item.original_image_path,
                    "reviewed_method": True,
                }
                for item in self.current_construction_plan.work_items
            ]
            selection = DamageRepairPlanner.build_reviewed_render_selection(
                self.summary.get("results", []), reviewed_plan_lines
            )
            items = selection["items"]
            self._log_event(
                f"渲染篩選：待渲染 {selection['render_count']} 張；"
                f"检测信息仅作诊断：未检出 {selection['skipped_no_detection']} 张；"
                f"识别失败 {selection['skipped_failed']} 张；"
                f"缺少方案 {selection['skipped_without_plan']} 张；"
                f"缺少原图路径 {selection.get('missing_source_path', 0)} 张"
            )
            if not items:
                self.render_results = []
                self.render_stop_button.setVisible(False)
                self.render_stop_button.setEnabled(False)
                self._set_workflow_stage(
                    "流程完成（沒有需要渲染的損傷圖片）",
                    system_status="已完成",
                    status_text="审核方案未包含可生成的施工工项，渲染阶段无法建立任务。",
                    log_message="渲染阶段：审核方案没有施工工项",
                    overall_progress=100,
                )
                self.current_generation_status.setText("审核方案没有施工工项")
                self._set_primary_button_idle()
                return
            self.render_results = []
            self.gallery.clear()
            render_output_dir = (self.output_dir / "修復渲染").resolve()
            self._log_event(f"修复渲染输出目录：{render_output_dir}")
            provider_name = "硅基流动" if self.api_config.get("repair_render_provider") == "siliconflow" else "FHL"
            self._log_event(f"修复渲染平台：{provider_name}")
            self.render_worker = RepairRenderWorker(
                items,
                render_output_dir,
                self.api_config,
                self.settings.render_prompt,
            )
            self.render_worker.item_done.connect(self.on_auto_render_item)
            self.render_worker.completed.connect(self.on_render_worker_completed)
            self.render_worker.cancelled.connect(self.on_render_worker_cancelled)
            self.render_worker.failed.connect(self.on_render_worker_failed)
            self.render_worker.finished.connect(self.on_render_worker_finished)
            self._set_primary_button_running(enabled=True)
            self.render_worker.start()

        def closeEvent(self, event) -> None:
            if self.plan_worker is not None and self.plan_worker.isRunning():
                self.status_label.setText("施工方案仍在生成；完成後再關閉窗口。")
                event.ignore()
                return
            if self.render_worker is not None and self.render_worker.isRunning():
                self.render_worker.stop()
                self._set_primary_button_running(enabled=False)
                if not self.render_worker.wait(3000):
                    self.status_label.setText("正在停止修复渲染；当前项目结束后再关闭窗口。")
                    event.ignore()
                    return
            if self.worker is not None and self.worker.isRunning():
                self.worker.stop()
                self.worker.wait(3000)
            if self.auto_worker is not None and self.auto_worker.isRunning():
                self.auto_worker.stop()
                self.auto_worker.wait(3000)
            super().closeEvent(event)


def main() -> int:
    if GUI_IMPORT_ERROR is not None:
        print(f"缺少 GUI 依賴，無法啟動：{GUI_IMPORT_ERROR}", file=sys.stderr)
        return 1
    try:
        from runtime.startup_diagnostics import write_startup_diagnostics

        write_startup_diagnostics()
    except Exception as exc:  # pragma: no cover - diagnostics must not block GUI
        print(f"启动诊断写入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
    app = QApplication(sys.argv)
    window = DamageWorkflowWindow()
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
