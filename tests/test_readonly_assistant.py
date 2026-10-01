from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from runtime.assistant.context import AssistantContextResolver, DetectionCatalog, ResolvedContext, record_detection_snapshot
from runtime.assistant.citations import render_knowledge_citations, resolve_knowledge_references
from runtime.assistant.service import AssistantService
from runtime.assistant.store import ConversationStore
from runtime.assistant.web_search import PublicWebSearchProvider, ResponsesWebSearchProvider


def _write_run(root: Path, source_name: str, date: str, *, image: str = "damage.jpg") -> Path:
    source = root / source_name
    output = source / f"构件视界_分析输出-{date}"
    output.mkdir(parents=True)
    payload = {
        "source_folder": str(source),
        "output_dir": str(output),
        "started_at": f"{date}T10:00:00+08:00",
        "finished_at": f"{date}T10:01:00+08:00",
        "status": "completed",
        "results": [{"image_name": image, "image_path": str(source / image), "damage_findings": [{"class_name": "Structural crack", "score": 0.88}]}],
    }
    (output / "batch_summary.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (output / "report.md").write_text("# 报告\n结构裂缝需要工程师复核。", encoding="utf-8")
    (output / "construction_plan.md").write_text("# 方案\n施工前复核裂缝活动性。", encoding="utf-8")
    return output


def test_responses_web_search_provider_requires_verified_url_and_title() -> None:
    response = SimpleNamespace(
        output_text="现行条文摘要",
        output=[{
            "type": "message",
            "annotations": [
                {"type": "url_citation", "url": "https://example.com/current", "title": "现行标准"},
                {"type": "url_citation", "url": "javascript:bad", "title": "无效来源"},
            ],
        }],
    )
    calls: list[dict] = []
    client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response)
    )
    provider = ResponsesWebSearchProvider(
        api_key="secret-not-persisted",
        base_url="https://example.test/v1",
        model="test-model",
        client=client,
    )
    results = provider.search("最新裂缝规范")
    assert [item.url for item in results] == ["https://example.com/current"]
    assert results[0].title == "现行标准"
    assert calls[0]["tools"] == [{"type": "web_search_preview"}]


def test_conversation_store_persists_messages_and_snapshot(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "assistant.sqlite3")
    conversation = store.create_conversation("测试对话")
    snapshot_id = store.save_snapshot({"run_id": "run-1"})
    store.add_message(conversation.conversation_id, "user", "问题")
    store.add_message(conversation.conversation_id, "assistant", "回答", source_manifest={"sources": []}, context_snapshot_id=snapshot_id)
    reopened = ConversationStore(tmp_path / "assistant.sqlite3")
    assert [message.content for message in reopened.messages(conversation.conversation_id)] == ["问题", "回答"]
    assert reopened.snapshot(snapshot_id) == {"run_id": "run-1"}


def test_resolver_supports_date_path_image_and_current_run(tmp_path: Path) -> None:
    first = _write_run(tmp_path, "bridge_a", "2026-09-10", image="a.jpg")
    second = _write_run(tmp_path, "bridge_b", "2026-09-11", image="b.jpg")
    resolver = AssistantContextResolver(current_output_dir=first, source_folder=tmp_path, archive_root=tmp_path / "archive")
    assert resolver.resolve("当前检测有什么问题").source_manifest["detection_runs"][0]["output_dir"] == str(first.resolve())
    assert resolver.resolve("2026年9月11日检测结果").source_manifest["detection_runs"][0]["output_dir"] == str(second.resolve())
    assert resolver.resolve("bridge_b 路径下的检测结果").source_manifest["detection_runs"][0]["output_dir"] == str(second.resolve())
    assert resolver.resolve("b.jpg 的检测结果").source_manifest["detection_runs"][0]["output_dir"] == str(second.resolve())


def test_resolver_supports_ordinal_and_combines_matching_context(tmp_path: Path) -> None:
    _write_run(tmp_path, "bridge_a", "2026-09-10", image="a.jpg")
    second = _write_run(tmp_path, "bridge_a", "2026-09-11", image="b.jpg")
    _write_run(tmp_path, "bridge_b", "2026-09-11", image="c.jpg")
    resolver = AssistantContextResolver(source_folder=tmp_path, archive_root=tmp_path / "archive")
    assert resolver.resolve("bridge_a 路径第二次检测").source_manifest["detection_runs"][0]["output_dir"] == str(second.resolve())
    resolved = resolver.resolve("2026年9月11日检测结果")
    assert not resolved.ambiguous
    assert resolved.prompt_context.count("检测批次：") == 2


def test_general_knowledge_question_does_not_block_on_multiple_runs(tmp_path: Path) -> None:
    _write_run(tmp_path, "bridge_a", "2026-09-10", image="a.jpg")
    _write_run(tmp_path, "bridge_a", "2026-09-11", image="b.jpg")
    _write_run(tmp_path, "bridge_b", "2026-09-11", image="c.jpg")
    resolver = AssistantContextResolver(source_folder=tmp_path, archive_root=tmp_path / "archive")
    resolved = resolver.resolve("裂缝损伤分为几个等级？是如何判定的")
    assert not resolved.ambiguous
    assert resolved.prompt_context == ""
    assert not resolved.warnings


def test_ambiguous_detection_context_is_non_blocking(tmp_path: Path) -> None:
    _write_run(tmp_path, "bridge_a", "2026-09-11", image="a.jpg")
    _write_run(tmp_path, "bridge_b", "2026-09-11", image="b.jpg")
    resolver = AssistantContextResolver(source_folder=tmp_path, archive_root=tmp_path / "archive")
    resolved = resolver.resolve("2026年9月11日检测结果有哪些裂缝")
    assert not resolved.ambiguous
    assert resolved.prompt_context
    assert not resolved.warnings


def test_service_answers_general_question_with_multiple_snapshots(tmp_path: Path) -> None:
    _write_run(tmp_path, "bridge_a", "2026-09-10", image="a.jpg")
    _write_run(tmp_path, "bridge_b", "2026-09-11", image="b.jpg")

    class Responses:
        @staticmethod
        def create(**_kwargs):
            return [SimpleNamespace(type="response.output_text.delta", delta="裂缝等级需要结合宽度、形态和结构影响综合判定。")]

    service = AssistantService(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        source_folder=tmp_path,
        client=SimpleNamespace(responses=Responses()),
        web_search_provider=SimpleNamespace(search=lambda _query, max_results=5: []),
    )
    service._knowledge_context = lambda _question: ("", [], [])  # type: ignore[method-assign]
    answer, manifest, _context = service.stream_answer("裂缝损伤分为几个等级？是如何判定的", [], on_delta=lambda _text: None)
    assert "综合判定" in answer
    assert not any("批次" in warning for warning in manifest["warnings"])


def test_catalog_does_not_scan_outside_registered_roots(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    _write_run(inside, "one", "2026-09-10")
    _write_run(outside, "two", "2026-09-11")
    runs = DetectionCatalog([inside]).scan()
    assert len(runs) == 1
    assert "inside" in runs[0].output_dir


def test_detection_snapshot_preserves_old_artifacts_after_output_overwrite(tmp_path: Path) -> None:
    output = _write_run(tmp_path, "bridge", "2026-09-10")
    summary_path = output / "batch_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["run_id"] = "run-old"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    archive_root = tmp_path / "assistant" / "detection_runs"
    record_detection_snapshot(summary_path, include_artifacts=True, archive_root=archive_root)
    (output / "report.md").write_text("# 新报告", encoding="utf-8")
    run = DetectionCatalog([], archive_root=archive_root).scan()[0]
    assert "结构裂缝需要工程师复核" in run.report_text


def test_service_registers_readonly_tools_only() -> None:
    assert "search_knowledge_base" in AssistantService.READONLY_TOOLS
    assert not any(term in tool for tool in AssistantService.READONLY_TOOLS for term in ("write", "edit", "delete", "shell", "powershell", "execute", "activate"))


def test_kb_citations_use_verified_filename_heading_and_page() -> None:
    refs = [
        {"source_type": "knowledge_base", "source_ref": "[KB:one:page:109]", "document_id": "one", "source_name": "民用建筑可靠性鉴定标准.pdf", "heading_path": ["第五章", "5.3 裂缝"]},
        {"source_type": "knowledge_base", "source_ref": "[KB:two:page:7]", "document_id": "two", "source_name": "工业建筑可靠性鉴定标准.pdf", "heading_path": []},
    ]
    text = "见[KB:one:page:109]、[KB:two:page:7]。"
    assert render_knowledge_citations(text, refs) == "见（《民用建筑可靠性鉴定标准.pdf》，第五章，5.3 裂缝，第109页）、（《工业建筑可靠性鉴定标准.pdf》，第7页）。"
    assert "[KB:one:page:109]" in refs[0]["source_ref"]


def test_kb_citations_omit_ambiguous_headings_and_unknown_sources() -> None:
    refs = [
        {"source_type": "knowledge_base", "source_ref": "[KB:one:page:3]", "source_name": "规范.pdf", "heading_path": ["第一章", "1.1 范围"]},
        {"source_type": "knowledge_base", "source_ref": "[KB:one:page:3]", "source_name": "规范.pdf", "heading_path": ["第一章", "1.2 要求"]},
    ]
    assert render_knowledge_citations("[KB:one:page:3] [KB:missing:page:9]", refs) == "（《规范.pdf》，第一章，第3页） （知识库来源未核实）"
    refs[1]["heading_path"] = []
    assert "第一章" not in render_knowledge_citations("[KB:one:page:3]", refs)
    assert render_knowledge_citations("内容[KB:one:pa", refs, streaming=True) == "内容"


def test_legacy_kb_reference_looks_up_active_catalog(monkeypatch) -> None:
    from runtime.assistant import citations

    monkeypatch.setattr(citations, "_indexed_metadata", lambda _refs: ({"one": "民用建筑可靠性鉴定标准.pdf"}, {"[KB:one:page:109]": [[], ["第五章"]]}))
    refs = resolve_knowledge_references([{"source_type": "knowledge_base", "source_ref": "[KB:one:page:109]", "document_id": "one"}])
    assert render_knowledge_citations("[KB:one:page:109]", refs) == "（《民用建筑可靠性鉴定标准.pdf》，第109页）"


def test_streaming_and_persisted_kb_citation_match(tmp_path: Path) -> None:
    marker = "[KB:doc:page:12]"
    ref = {"source_type": "knowledge_base", "source_ref": marker, "document_id": "doc", "source_name": "规范.pdf", "heading_path": ["第二章"]}
    class Responses:
        @staticmethod
        def create(**_kwargs):
            return [SimpleNamespace(type="response.output_text.delta", delta=piece) for piece in ("参见[KB:doc:pa", "ge:12]", "。")]

    service = AssistantService(api_key="test", base_url="https://example.test/v1", model="test", source_folder=tmp_path, client=SimpleNamespace(responses=Responses()))
    service._knowledge_context = lambda _question: (f"{marker}\n内容", [ref], [])  # type: ignore[method-assign]
    updates: list[str] = []
    answer, manifest, _ = service.stream_answer("裂缝如何处理", [], on_delta=updates.append)
    assert updates == ["参见", "参见（《规范.pdf》，第二章，第12页）", "参见（《规范.pdf》，第二章，第12页）。"]
    assert answer == updates[-1]
    assert manifest["sources"][0]["source_ref"] == marker


def test_service_cancel_before_first_delta_is_not_an_empty_answer_failure(tmp_path: Path) -> None:
    calls: list[dict] = []

    class Responses:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return []

    stop_event = threading.Event()
    stop_event.set()
    service = AssistantService(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        source_folder=tmp_path,
        client=SimpleNamespace(responses=Responses()),
        web_search_provider=SimpleNamespace(search=lambda _query, max_results=5: []),
    )
    service._knowledge_context = lambda _question: ("", [], [])  # type: ignore[method-assign]
    answer, manifest, _context = service.stream_answer("问题", [], on_delta=lambda _text: None, stop_event=stop_event)
    assert answer == ""
    assert manifest["cancelled"] is True
    assert calls == []


def test_service_cancel_discards_partial_answer(tmp_path: Path) -> None:
    stop_event = threading.Event()

    class Responses:
        @staticmethod
        def create(**_kwargs):
            def events():
                yield SimpleNamespace(type="response.output_text.delta", delta="部分回答")
                stop_event.set()
                yield SimpleNamespace(type="response.output_text.delta", delta="不应接收")

            return events()

    updates: list[str] = []
    service = AssistantService(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        source_folder=tmp_path,
        client=SimpleNamespace(responses=Responses()),
        web_search_provider=SimpleNamespace(search=lambda _query, max_results=5: []),
    )
    service._knowledge_context = lambda _question: ("", [], [])  # type: ignore[method-assign]
    answer, manifest, _context = service.stream_answer("问题", [], on_delta=updates.append, stop_event=stop_event)
    assert updates == ["部分回答"]
    assert answer == ""
    assert manifest["cancelled"] is True


def test_service_preserves_provider_failure_without_cancellation(tmp_path: Path) -> None:
    class Responses:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError("provider unavailable")

    service = AssistantService(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        source_folder=tmp_path,
        client=SimpleNamespace(responses=Responses()),
        web_search_provider=SimpleNamespace(search=lambda _query, max_results=5: []),
    )
    service._knowledge_context = lambda _question: ("", [], [])  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="provider unavailable"):
        service.stream_answer("问题", [], on_delta=lambda _text: None, stop_event=threading.Event())


def test_web_routing_distinguishes_historical_detection_from_explicit_search() -> None:
    assert AssistantService._needs_web("2026年9月10日的检测结果") is False
    assert AssistantService._needs_web("请联网搜索现行裂缝修复规范") is True


def test_service_streams_freeform_answer_and_keeps_web_citation(tmp_path: Path) -> None:
    class Event:
        def __init__(self, event_type: str, delta: str = "", payload: dict | None = None) -> None:
            self.type = event_type
            self.delta = delta
            self.payload = payload or {"type": event_type}

        def model_dump(self) -> dict:
            return self.payload

    calls: list[dict] = []

    class Responses:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return [
                Event("response.output_text.delta", "可采用保守复核。"),
                Event("response.output_item.done", payload={"type": "response.output_item.done", "annotations": [{"type": "url_citation", "url": "https://example.com/spec", "title": "规范来源"}]}),
            ]

    client = SimpleNamespace(responses=Responses())
    web_provider = SimpleNamespace(search=lambda _query, max_results=5: [])
    service = AssistantService(api_key="test", base_url="https://example.test/v1", model="test-model", source_folder=tmp_path, client=client, web_search_provider=web_provider)
    service._knowledge_context = lambda _question: ("", [], [])  # type: ignore[method-assign]
    updates: list[str] = []
    answer, manifest, _context = service.stream_answer("请联网搜索最新裂缝修复资料", [], on_delta=updates.append)
    assert answer == "可采用保守复核。"
    assert calls[0]["tools"] == [{"type": "web_search_preview"}]
    assert manifest["web_search_used"] is True
    assert any(item.get("url") == "https://example.com/spec" for item in manifest["sources"])


def test_public_web_search_parser_returns_auditable_metadata() -> None:
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fguide">裂缝修复指南</a>
      <a class="result__snippet">公开资料摘要</a>
    </div>
    """
    provider = PublicWebSearchProvider(transport=lambda _url, _timeout: html)
    results = provider.search("裂缝修复")
    assert len(results) == 1
    assert results[0].url == "https://example.com/guide"
    assert results[0].domain == "example.com"
    assert results[0].snippet == "公开资料摘要"
    assert results[0].accessed_at


def test_assistant_identity_uses_engineering_assistant_name(tmp_path: Path) -> None:
    service = AssistantService(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
        source_folder=tmp_path,
        web_search_provider=SimpleNamespace(search=lambda _query, max_results=5: []),
    )
    instructions = service._instructions(ResolvedContext("当前没有检测上下文。"), "", False)
    assert "工程问答助手" in instructions
    assert "只读工程问答助手" not in instructions


def test_dialog_hides_source_manifest_from_conversation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "app-data"))
    from PySide6.QtWidgets import QApplication

    from runtime.assistant.dialog import AssistantDialog
    from runtime.settings_models import AppSettings

    app = QApplication.instance() or QApplication([])
    dialog = AssistantDialog(
        None,
        api_config={"responses_url": "https://example.test/v1", "responses_key": "", "responses_model": "test"},
        settings=AppSettings(),
        current_output_getter=lambda: None,
    )
    assert dialog.current_conversation is not None
    dialog.store.add_message(
        dialog.current_conversation.conversation_id,
        "assistant",
        "这是回答正文。我是只读工程问答助手。",
        source_manifest={
            "sources": [{"source_ref": "[KB:doc:page:1]"}],
            "warnings": ["内部检索告警"],
        },
    )
    dialog._render_messages()
    rendered = dialog.messages_view.toPlainText()
    assert "这是回答正文。" in rendered
    assert "我是工程问答助手" in rendered
    assert "只读工程问答助手" not in rendered
    assert "依据详情" not in rendered
    assert "[KB:doc:page:1]" not in rendered
    assert "内部检索告警" not in rendered
    assert dialog.status_label.text().startswith("工程问答助手：")
    dialog.close()
    app.processEvents()


def test_dialog_stop_is_silent_and_allows_new_conversation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "app-data"))
    from PySide6.QtWidgets import QApplication

    from runtime.assistant.dialog import AssistantDialog
    from runtime.settings_models import AppSettings

    app = QApplication.instance() or QApplication([])
    dialog = AssistantDialog(
        None,
        api_config={"responses_url": "https://example.test/v1", "responses_key": "", "responses_model": "test"},
        settings=AppSettings(),
        current_output_getter=lambda: None,
    )
    assert dialog.current_conversation is not None
    origin_id = dialog.current_conversation.conversation_id
    dialog.store.add_message(origin_id, "user", "微信桌面端最新版本有什么新功能")
    dialog._render_messages()

    request_id = "request-cancelled"
    stop_event = threading.Event()
    dialog._workers[request_id] = SimpleNamespace()
    dialog._stop_events[request_id] = stop_event
    dialog._conversation_requests[origin_id] = request_id
    dialog._request_conversations[request_id] = origin_id
    dialog._pending_answers[request_id] = ""
    dialog._conversation_status[origin_id] = dialog.RUNNING_STATUS
    dialog._update_controls()
    dialog._stream_update(request_id, origin_id, "部分回答")
    assert "部分回答" in dialog.messages_view.toPlainText()

    dialog._stop()
    assert stop_event.is_set()
    assert dialog.new_button.isEnabled()
    assert dialog.conversation_list.isEnabled()
    assert dialog.status_label.text() == ""
    assert "部分回答" not in dialog.messages_view.toPlainText()

    dialog._new_conversation()
    assert dialog.current_conversation is not None
    new_id = dialog.current_conversation.conversation_id
    assert new_id != origin_id
    dialog._answer_failed(request_id, origin_id, "RuntimeError: 问答模型未返回文本")
    dialog._answer_completed(request_id, origin_id, "迟到回答", {"cancelled": False}, {})
    assert [item.role for item in dialog.store.messages(origin_id)] == ["user"]
    assert dialog.store.messages(new_id) == []
    assert dialog.messages_view.toPlainText() == ""

    dialog._worker_finished(request_id, origin_id)
    assert request_id not in dialog._workers
    assert dialog.send_button.isEnabled()
    assert "回答失败" not in dialog.status_label.text()
    dialog.close()
    app.processEvents()


def test_dialog_starts_parallel_workers_and_keeps_streams_isolated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "app-data"))
    from PySide6.QtWidgets import QApplication

    from runtime.assistant import dialog as dialog_module
    from runtime.settings_models import AppSettings

    class FakeSignal:
        def __init__(self) -> None:
            self.slots = []

        def connect(self, slot) -> None:
            self.slots.append(slot)

    class FakeWorker:
        def __init__(self, _service, _question, _history, stop_event, *, request_id, conversation_id) -> None:
            self.stop_event = stop_event
            self.request_id = request_id
            self.conversation_id = conversation_id
            self.delta = FakeSignal()
            self.completed = FakeSignal()
            self.failed = FakeSignal()
            self.finished = FakeSignal()
            self.started = False

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(dialog_module, "AssistantWorker", FakeWorker)
    app = QApplication.instance() or QApplication([])
    dialog = dialog_module.AssistantDialog(
        None,
        api_config={"responses_url": "https://example.test/v1", "responses_key": "test", "responses_model": "test"},
        settings=AppSettings(),
        current_output_getter=lambda: None,
    )
    monkeypatch.setattr(dialog, "_make_service", lambda: SimpleNamespace())

    assert dialog.current_conversation is not None
    conversation_a = dialog.current_conversation.conversation_id
    dialog.input_edit.setPlainText("问题 A")
    dialog._send()
    request_a = dialog._conversation_requests[conversation_a]
    assert dialog._workers[request_a].started
    assert dialog.new_button.isEnabled()
    assert dialog.conversation_list.isEnabled()

    dialog._new_conversation()
    assert dialog.current_conversation is not None
    conversation_b = dialog.current_conversation.conversation_id
    assert conversation_b != conversation_a
    assert dialog.send_button.isEnabled()
    dialog.input_edit.setPlainText("问题 B")
    dialog._send()
    request_b = dialog._conversation_requests[conversation_b]
    assert request_b != request_a
    assert len(dialog._workers) == 2

    dialog._stream_update(request_a, conversation_a, "回答 A 进行中")
    assert "回答 A 进行中" not in dialog.messages_view.toPlainText()
    dialog._stream_update(request_b, conversation_b, "回答 B 进行中")
    assert "回答 B 进行中" in dialog.messages_view.toPlainText()

    for index in range(dialog.conversation_list.count()):
        if dialog.conversation_list.item(index).data(dialog_module.Qt.UserRole) == conversation_a:
            dialog.conversation_list.setCurrentRow(index)
            break
    assert "回答 A 进行中" in dialog.messages_view.toPlainText()
    assert "回答 B 进行中" not in dialog.messages_view.toPlainText()

    dialog._answer_completed(request_b, conversation_b, "回答 B 完成", {"cancelled": False, "trace_id": "b"}, {})
    assert any(item.content == "回答 B 完成" for item in dialog.store.messages(conversation_b))
    assert not any(item.content == "回答 B 完成" for item in dialog.store.messages(conversation_a))

    dialog._stop()
    assert dialog._stop_events[request_a].is_set()
    assert not dialog._stop_events[request_b].is_set()
    assert request_b not in dialog._cancelled_request_ids

    dialog._worker_finished(request_a, conversation_a)
    dialog._worker_finished(request_b, conversation_b)
    dialog.close()
    app.processEvents()


def test_dialog_close_requests_stop_for_all_active_workers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "app-data"))
    from PySide6.QtWidgets import QApplication

    from runtime.assistant.dialog import AssistantDialog
    from runtime.settings_models import AppSettings

    app = QApplication.instance() or QApplication([])
    dialog = AssistantDialog(
        None,
        api_config={"responses_url": "https://example.test/v1", "responses_key": "test", "responses_model": "test"},
        settings=AppSettings(),
        current_output_getter=lambda: None,
    )
    stops = {"a": threading.Event(), "b": threading.Event()}
    dialog._workers.update({"a": SimpleNamespace(), "b": SimpleNamespace()})
    dialog._stop_events.update(stops)
    dialog._pending_answers.update({"a": "A", "b": "B"})
    event = SimpleNamespace(ignore=lambda: None)
    dialog.closeEvent(event)
    assert dialog._closing_after_workers
    assert all(item.is_set() for item in stops.values())
    assert dialog._pending_answers == {"a": "", "b": ""}
    assert dialog._cancelled_request_ids == {"a", "b"}
    assert "停止全部回答" in dialog.status_label.text()

    dialog._workers.clear()
    dialog._stop_events.clear()
    dialog._closing_after_workers = False
    dialog.close()
    app.processEvents()


def test_dialog_renders_and_copies_legacy_kb_citation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("YOLO11_DAMAGE_DATA_DIR", str(tmp_path / "app-data"))
    from PySide6.QtWidgets import QApplication

    from runtime.assistant import citations
    from runtime.assistant.dialog import AssistantDialog
    from runtime.settings_models import AppSettings

    monkeypatch.setattr(citations, "_indexed_metadata", lambda _refs: ({"doc": "民用建筑可靠性鉴定标准.pdf"}, {"[KB:doc:page:109]": [[], []]}))
    app = QApplication.instance() or QApplication([])
    dialog = AssistantDialog(
        None,
        api_config={"responses_url": "https://example.test/v1", "responses_key": "", "responses_model": "test"},
        settings=AppSettings(), current_output_getter=lambda: None,
    )
    assert dialog.current_conversation is not None
    dialog.store.add_message(
        dialog.current_conversation.conversation_id, "assistant", "参考[KB:doc:page:109]。",
        source_manifest={"sources": [{"source_type": "knowledge_base", "source_ref": "[KB:doc:page:109]", "document_id": "doc"}]},
    )
    dialog._render_messages()
    assert "《民用建筑可靠性鉴定标准.pdf》" in dialog.messages_view.toPlainText()
    assert "第109页" in dialog.messages_view.toPlainText()
    assert "[KB:" not in dialog.messages_view.toPlainText()
    dialog._copy_last_answer()
    assert QApplication.clipboard().text() == "参考（《民用建筑可靠性鉴定标准.pdf》，第109页）。"
    dialog.close()
    app.processEvents()


def test_message_composer_enter_sends_and_ctrl_enter_adds_newline(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from runtime.assistant.dialog import MessageComposer

    app = QApplication.instance() or QApplication([])
    composer = MessageComposer()
    composer.show()
    composer.setFocus()
    sent: list[str] = []
    composer.send_requested.connect(lambda: sent.append(composer.toPlainText()))

    composer.setPlainText("第一条")
    QTest.keyClick(composer, Qt.Key_Return)
    assert sent == ["第一条"]
    assert composer.toPlainText() == "第一条"

    composer.moveCursor(composer.textCursor().MoveOperation.End)
    QTest.keyClick(composer, Qt.Key_Return, Qt.ControlModifier)
    assert sent == ["第一条"]
    assert composer.toPlainText() == "第一条\n"

    QTest.keyClick(composer, Qt.Key_Enter)
    assert sent == ["第一条", "第一条\n"]

    QTest.keyClick(composer, Qt.Key_Enter, Qt.ControlModifier)
    assert sent == ["第一条", "第一条\n"]
    assert composer.toPlainText() == "第一条\n\n"
    composer.close()
    app.processEvents()
