"""PySide6 conversation dialog for the read-only assistant."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from runtime.app_paths import user_data_root
from runtime.assistant.service import AssistantService
from runtime.assistant.citations import render_knowledge_citations, resolve_knowledge_references
from runtime.assistant.store import Conversation, ConversationStore

try:
    from PySide6.QtCore import QThread, Qt, Signal
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QSplitter,
        QTextBrowser,
        QVBoxLayout,
        QWidget,
    )
except Exception:  # pragma: no cover - imported only by the desktop GUI
    QThread = object  # type: ignore[assignment,misc]
    QDialog = object  # type: ignore[assignment,misc]


class MessageComposer(QPlainTextEdit):
    """Message editor with chat-style Enter and Ctrl+Enter behavior."""

    send_requested = Signal()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ControlModifier:
                self.insertPlainText("\n")
            else:
                self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AssistantWorker(QThread):
    delta = Signal(str, str, str)
    completed = Signal(str, str, str, object, object)
    failed = Signal(str, str, str)

    def __init__(self, service: AssistantService, question: str, history: list[dict[str, str]], stop_event: threading.Event, *, request_id: str, conversation_id: str) -> None:
        super().__init__()
        self.service = service
        self.question = question
        self.history = history
        self.stop_event = stop_event
        self.request_id = request_id
        self.conversation_id = conversation_id

    def run(self) -> None:  # pragma: no cover - exercised by the desktop runtime
        try:
            answer, manifest, context = self.service.stream_answer(
                self.question,
                self.history,
                on_delta=lambda text: self.delta.emit(self.request_id, self.conversation_id, text),
                stop_event=self.stop_event,
            )
            self.completed.emit(self.request_id, self.conversation_id, answer, manifest, context.snapshot)
        except Exception as exc:
            self.failed.emit(self.request_id, self.conversation_id, f"{type(exc).__name__}: {exc}")


class AssistantDialog(QDialog):
    """Persistent engineering-assistant conversation UI."""

    IDLE_STATUS = "工程问答助手：可读取检测结果、报告、方案、知识库和公开资料，不会修改项目文件。"
    RUNNING_STATUS = "正在读取当前项目、历史检测、知识库并生成回答…"

    def __init__(self, parent: Any, *, api_config: dict[str, str], settings: Any, current_output_getter: Callable[[], Path | None]) -> None:
        super().__init__(parent)
        self.setWindowTitle("问答助手")
        self.resize(1220, 780)
        self.setMinimumSize(920, 600)
        self.settings = settings
        self.api_config = api_config
        self.current_output_getter = current_output_getter
        self.store = ConversationStore(user_data_root() / "assistant" / "conversations.sqlite3")
        self.current_conversation: Conversation | None = None
        self._workers: dict[str, AssistantWorker] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._conversation_requests: dict[str, str] = {}
        self._request_conversations: dict[str, str] = {}
        self._pending_answers: dict[str, str] = {}
        self._conversation_status: dict[str, str] = {}
        self._cancelled_request_ids: set[str] = set()
        self._closing_after_workers = False
        self._build_ui()
        self._refresh_conversations()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        splitter = QSplitter(Qt.Horizontal)
        self.conversation_list = QListWidget()
        self.conversation_list.setMinimumWidth(220)
        self.conversation_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.conversation_list.currentItemChanged.connect(self._select_conversation)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("对话记录"))
        self.new_button = QPushButton("＋ 新建对话")
        self.new_button.clicked.connect(self._new_conversation)
        left_layout.addWidget(self.new_button)
        left_layout.addWidget(self.conversation_list, 1)
        self.archive_button = QPushButton("归档当前对话")
        self.archive_button.clicked.connect(self._archive_current)
        left_layout.addWidget(self.archive_button)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel("新对话")
        self.title_label.setObjectName("assistantTitle")
        right_layout.addWidget(self.title_label)
        self.messages_view = QTextBrowser()
        self.messages_view.setOpenExternalLinks(True)
        self.messages_view.setReadOnly(True)
        right_layout.addWidget(self.messages_view, 1)
        self.status_label = QLabel(self.IDLE_STATUS)
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)
        self.input_edit = MessageComposer()
        self.input_edit.setPlaceholderText("输入问题，例如：2026 年 9 月 10 日第二次检测中有哪些结构裂缝？")
        self.input_edit.setMaximumHeight(120)
        self.input_edit.send_requested.connect(self._send)
        right_layout.addWidget(self.input_edit)
        actions = QHBoxLayout()
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self._send)
        actions.addWidget(self.send_button)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        actions.addWidget(self.stop_button)
        copy_button = QPushButton("复制")
        copy_button.clicked.connect(self._copy_last_answer)
        actions.addWidget(copy_button)
        actions.addStretch(1)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([260, 900])
        root.addWidget(splitter, 1)

    @staticmethod
    def _display_content(content: str) -> str:
        """Apply user-facing terminology without rewriting stored history."""
        return str(content).replace("只读工程问答助手", "工程问答助手")

    def _refresh_conversations(self, *, select_id: str = "") -> None:
        self.conversation_list.blockSignals(True)
        self.conversation_list.clear()
        conversations = self.store.list_conversations()
        for conversation in conversations:
            item = QListWidgetItem(conversation.title)
            item.setData(Qt.UserRole, conversation.conversation_id)
            item.setToolTip(conversation.updated_at)
            self.conversation_list.addItem(item)
        self.conversation_list.blockSignals(False)
        if select_id:
            for index in range(self.conversation_list.count()):
                if self.conversation_list.item(index).data(Qt.UserRole) == select_id:
                    self.conversation_list.setCurrentRow(index)
                    return
        if self.conversation_list.count():
            self.conversation_list.setCurrentRow(0)
        else:
            self._new_conversation()

    def _new_conversation(self) -> None:
        conversation = self.store.create_conversation()
        self.current_conversation = conversation
        self._refresh_conversations(select_id=conversation.conversation_id)
        self._render_messages()

    def _select_conversation(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if current is None:
            return
        conversation_id = str(current.data(Qt.UserRole) or "")
        for item in self.store.list_conversations():
            if item.conversation_id == conversation_id:
                self.current_conversation = item
                break
        self._render_messages()
        self._update_controls()

    def _render_messages(self, *, pending_answer: str | None = None) -> None:
        if self.current_conversation is None:
            self.messages_view.clear()
            return
        conversation_id = self.current_conversation.conversation_id
        if pending_answer is None:
            request_id = self._conversation_requests.get(conversation_id, "")
            pending_answer = self._pending_answers.get(request_id, "")
        self.title_label.setText(self.current_conversation.title)
        lines: list[str] = []
        for message in self.store.messages(conversation_id):
            label = "用户" if message.role == "user" else "助手"
            content = self._display_content(message.content)
            if message.role == "assistant" and "[KB:" in content:
                refs = resolve_knowledge_references(list((message.source_manifest or {}).get("sources", [])))
                content = render_knowledge_citations(content, refs)
            lines.extend([f"### {label}", "", content, ""])
        if pending_answer:
            lines.extend(["### 助手", "", self._display_content(pending_answer), ""])
        self.messages_view.setMarkdown("\n".join(lines))
        self.messages_view.verticalScrollBar().setValue(self.messages_view.verticalScrollBar().maximum())

    def _update_controls(self) -> None:
        conversation_id = self.current_conversation.conversation_id if self.current_conversation else ""
        request_id = self._conversation_requests.get(conversation_id, "")
        active = bool(request_id and request_id in self._workers and request_id not in self._cancelled_request_ids)
        busy = bool(request_id and request_id in self._workers)
        self.send_button.setEnabled(bool(conversation_id) and not busy and not self._closing_after_workers)
        self.stop_button.setEnabled(active and not self._closing_after_workers)
        self.new_button.setEnabled(not self._closing_after_workers)
        self.conversation_list.setEnabled(not self._closing_after_workers)
        self.archive_button.setEnabled(bool(conversation_id) and not busy and not self._closing_after_workers)
        if self._closing_after_workers:
            self.status_label.setText("正在停止全部回答，停止后关闭窗口…")
        elif conversation_id:
            default_status = self.RUNNING_STATUS if active else self.IDLE_STATUS
            self.status_label.setText(self._conversation_status.get(conversation_id, default_status))
        else:
            self.status_label.setText(self.IDLE_STATUS)

    def _history(self) -> list[dict[str, str]]:
        if self.current_conversation is None:
            return []
        return [{"role": message.role, "content": message.content} for message in self.store.messages(self.current_conversation.conversation_id) if message.role in {"user", "assistant"}][-12:]

    def _make_service(self) -> AssistantService:
        output_dir = self.current_output_getter()
        source_folder = output_dir.parent if output_dir else None
        kb_ids = list(getattr(self.settings.knowledge_base, "enabled_document_ids", []) or [])
        return AssistantService(api_key=str(self.api_config.get("responses_key", "")), base_url=str(self.api_config.get("responses_url", "")), model=str(self.api_config.get("responses_model", "")), kb_document_ids=kb_ids, current_output_dir=output_dir, source_folder=source_folder)

    def _send(self) -> None:
        question = self.input_edit.toPlainText().strip()
        if not question or self._closing_after_workers:
            return
        if self.current_conversation is None:
            self._new_conversation()
        assert self.current_conversation is not None
        conversation_id = self.current_conversation.conversation_id
        if conversation_id in self._conversation_requests:
            return
        history = self._history()
        self.store.add_message(conversation_id, "user", question)
        if self.current_conversation.title == "新对话":
            title = question.replace("\n", " ").strip()[:38]
            self.store.rename_conversation(conversation_id, title)
            self.current_conversation = Conversation(
                conversation_id,
                title,
                self.current_conversation.created_at,
                self.current_conversation.updated_at,
                self.current_conversation.archived,
            )
            self.title_label.setText(title)
        self.input_edit.clear()
        self._render_messages()
        request_id = str(uuid.uuid4())
        stop_event = threading.Event()
        worker = AssistantWorker(
            self._make_service(),
            question,
            history,
            stop_event,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        self._workers[request_id] = worker
        self._stop_events[request_id] = stop_event
        self._conversation_requests[conversation_id] = request_id
        self._request_conversations[request_id] = conversation_id
        self._pending_answers[request_id] = ""
        self._conversation_status[conversation_id] = self.RUNNING_STATUS
        worker.delta.connect(self._stream_update)
        worker.completed.connect(self._answer_completed)
        worker.failed.connect(self._answer_failed)
        worker.finished.connect(lambda rid=request_id, cid=conversation_id: self._worker_finished(rid, cid))
        self._update_controls()
        worker.start()

    def _stream_update(self, request_id: str, conversation_id: str, text: str) -> None:
        if request_id not in self._workers or request_id in self._cancelled_request_ids:
            return
        if self._conversation_requests.get(conversation_id) != request_id:
            return
        self._pending_answers[request_id] = text
        if self.current_conversation is not None and self.current_conversation.conversation_id == conversation_id:
            self._render_messages()

    def _answer_completed(self, request_id: str, conversation_id: str, answer: str, manifest: object, snapshot: object) -> None:
        manifest_dict = dict(manifest) if isinstance(manifest, dict) else {}
        if request_id not in self._workers or request_id in self._cancelled_request_ids or manifest_dict.get("cancelled"):
            return
        if self._conversation_requests.get(conversation_id) != request_id:
            return
        snapshot_dict = dict(snapshot) if isinstance(snapshot, dict) else {}
        snapshot_id = self.store.save_snapshot(snapshot_dict)
        self.store.add_message(conversation_id, "assistant", answer, status="completed", trace_id=str(manifest_dict.get("trace_id", "")), source_manifest=manifest_dict, context_snapshot_id=snapshot_id)
        self._pending_answers[request_id] = ""
        self._conversation_status[conversation_id] = "回答完成。"
        if self.current_conversation is not None and self.current_conversation.conversation_id == conversation_id:
            self._render_messages()
            self._update_controls()

    def _answer_failed(self, request_id: str, conversation_id: str, message: str) -> None:
        if request_id not in self._workers or request_id in self._cancelled_request_ids:
            return
        if self._conversation_requests.get(conversation_id) != request_id:
            return
        self.store.add_message(conversation_id, "assistant", f"问答失败：{message}", status="failed")
        self._pending_answers[request_id] = ""
        self._conversation_status[conversation_id] = "回答失败；请检查 API 设置或稍后重试。"
        if self.current_conversation is not None and self.current_conversation.conversation_id == conversation_id:
            self._render_messages()
            self._update_controls()

    def _worker_finished(self, request_id: str, conversation_id: str) -> None:
        if request_id not in self._workers:
            return
        was_cancelled = request_id in self._cancelled_request_ids
        self._workers.pop(request_id, None)
        self._stop_events.pop(request_id, None)
        self._pending_answers.pop(request_id, None)
        self._request_conversations.pop(request_id, None)
        if self._conversation_requests.get(conversation_id) == request_id:
            self._conversation_requests.pop(conversation_id, None)
        self._cancelled_request_ids.discard(request_id)
        if was_cancelled:
            self._conversation_status[conversation_id] = ""
        if self.current_conversation is not None and self.current_conversation.conversation_id == conversation_id:
            self._render_messages()
        self._update_controls()
        if self._closing_after_workers and not self._workers:
            self._closing_after_workers = False
            self.close()

    def _stop(self) -> None:
        if self.current_conversation is None:
            return
        conversation_id = self.current_conversation.conversation_id
        request_id = self._conversation_requests.get(conversation_id, "")
        if not request_id or request_id not in self._workers:
            return
        self._cancelled_request_ids.add(request_id)
        stop_event = self._stop_events.get(request_id)
        if stop_event is not None:
            stop_event.set()
        self._pending_answers[request_id] = ""
        self._conversation_status[conversation_id] = ""
        self._render_messages()
        self._update_controls()

    def _copy_last_answer(self) -> None:
        if self.current_conversation is None:
            return
        answers = [item for item in self.store.messages(self.current_conversation.conversation_id) if item.role == "assistant"]
        if answers:
            latest = answers[-1]
            refs = resolve_knowledge_references(list((latest.source_manifest or {}).get("sources", []))) if "[KB:" in latest.content else []
            QApplication.clipboard().setText(render_knowledge_citations(self._display_content(latest.content), refs))

    def _archive_current(self) -> None:
        if self.current_conversation is None:
            return
        conversation_id = self.current_conversation.conversation_id
        if conversation_id in self._conversation_requests:
            return
        self.store.archive_conversation(conversation_id)
        self.current_conversation = None
        self._refresh_conversations()

    def closeEvent(self, event: Any) -> None:  # pragma: no cover - desktop lifecycle
        if self._workers:
            self._closing_after_workers = True
            for request_id, stop_event in list(self._stop_events.items()):
                self._cancelled_request_ids.add(request_id)
                stop_event.set()
                self._pending_answers[request_id] = ""
            self._update_controls()
            event.ignore()
            return
        super().closeEvent(event)
