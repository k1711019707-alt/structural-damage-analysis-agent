from __future__ import annotations

import json
import zipfile
from pathlib import Path


def test_settings_round_trip_and_redacted_export(tmp_path: Path) -> None:
    from runtime.settings_models import AppSettings
    from runtime.settings_store import SettingsStore

    path = tmp_path / "gui_settings.json"
    legacy = tmp_path / "gui_api_config.json"
    legacy.write_text(json.dumps({"responses_url": "https://example.test", "responses_key": "secret", "responses_model": "demo", "fhl_url": "https://fhl.test", "fhl_key": "fhl-secret"}), encoding="utf-8")
    store = SettingsStore(path, legacy)
    settings = store.load()
    assert settings.api.responses_key == "secret"
    assert path.is_file()
    assert legacy.with_suffix(".json.legacy-backup").is_file()

    exported = store.export_payload(settings)
    assert exported["api"]["responses_key"] == ""
    assert exported["api"]["fhl_key"] == ""
    assert exported["api"]["siliconflow_key"] == ""
    assert settings.api.repair_render_provider == "fhl"
    assert settings.api.siliconflow_url == "https://api.siliconflow.cn/v1"
    assert settings.api.siliconflow_model == "Qwen/Qwen-Image-Edit-2509"
    assert "原始损伤照片为唯一场景依据" in settings.render_prompt
    assert "不得拉直、校正或掩盖结构变形" in settings.render_prompt
    assert "不代表施工放行" in settings.render_prompt
    store.save(settings)
    loaded = AppSettings.from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert loaded.to_dict()["api"]["responses_model"] == "demo"
    loaded.generation_profiles["施工方案"].knowledge_base_folder_ids = ["folder-a", "folder-b"]
    loaded.render_prompt = "自定义渲染提示词"
    assert AppSettings.from_dict(loaded.to_dict()).generation_profiles["施工方案"].knowledge_base_folder_ids == ["folder-a", "folder-b"]
    assert AppSettings.from_dict(loaded.to_dict()).render_prompt == "自定义渲染提示词"
    loaded.api.repair_render_provider = "siliconflow"
    loaded.api.siliconflow_key = "silicon-secret"
    restored = AppSettings.from_dict(loaded.to_dict())
    assert restored.api.repair_render_provider == "siliconflow"
    assert restored.api.siliconflow_key == "silicon-secret"
    legacy_payload = loaded.to_dict()
    legacy_payload["generation_profiles"]["造價預估"] = {"name": "造價預估", "prompt": "legacy"}
    assert set(AppSettings.from_dict(legacy_payload).generation_profiles) == {"損傷分析報告", "施工方案"}


def test_generation_context_excludes_unselected_documents() -> None:
    from runtime.generation_context import build_generation_context
    from runtime.knowledge_base import KnowledgeBaseChunk
    from runtime.settings_models import default_profiles

    profile = default_profiles()["施工方案"]
    profile.knowledge_base_folder_ids = ["施工-folder"]
    context = build_generation_context(
        profile,
        {"results": [{"damage_findings": []}]},
        settings_snapshot_id="snapshot-1",
        retrieved_chunks=[KnowledgeBaseChunk("chunk-1", "doc-a", "page:1", "可用工法"),],
    )
    assert context.knowledge_base_used is True
    assert "[KB:doc-a:page:1]" in context.prompt
    assert "doc-b" not in context.prompt
    manifest = context.manifest(model="demo")
    assert manifest["retrieved_chunks"][0]["chunk_id"] == "chunk-1"
    assert "text" not in manifest["retrieved_chunks"][0]
    assert manifest["knowledge_base_scope_folder_ids"] == ["施工-folder"]


def test_runtime_generation_context_preserves_hybrid_warning_and_evidence_roles() -> None:
    from runtime.generation_context import build_generation_context
    from runtime.knowledge_base import KnowledgeBaseChunk
    from runtime.settings_models import default_profiles

    profile = default_profiles()["損傷分析報告"]
    child = KnowledgeBaseChunk("c1", "doc-a", "page:1", "裂缝宽度应复核", metadata={"retrieval_role": "retrieval", "retrieval_channels": ["bm25", "semantic"], "anchor": True, "chunk_type": "paragraph", "text_raw": "裂缝宽度应复核"})
    parent = KnowledgeBaseChunk("p1", "doc-a", "page:1", "裂缝宽度应复核", metadata={"retrieval_role": "context_only", "context_for": "c1", "text_raw": "裂缝宽度应复核"})
    context = build_generation_context(
        profile,
        {"results": []},
        settings_snapshot_id="snapshot-hybrid",
        retrieved_chunks=[child, parent],
        retrieval_mode="hybrid_semantic",
        retrieval_warnings=["vector index stale; lexical fallback retained"],
        anchors=[{"chunk_id": "c1"}],
        context_groups=[{"anchor_chunk_ids": ["c1"], "chunk_ids": ["c1", "p1"]}],
    )
    assert context.retrieval_warnings == ["vector index stale; lexical fallback retained"]
    assert "知识库检索模式：hybrid_semantic" in context.prompt
    assert "重复上下文已省略" in context.prompt
    assert context.evidence_groups[0]["items"][0]["role"] == "anchor"
    manifest = context.manifest(model="demo")
    assert manifest["retrieval_warnings"]
    assert manifest["evidence_groups"][0]["items"][1]["included"] is False


def test_knowledge_base_reuses_an_already_indexed_document(monkeypatch, tmp_path: Path) -> None:
    import runtime.knowledge_base as knowledge_base

    source = tmp_path / "reference.docx"
    source.write_bytes(b"docx-content")
    calls = {"count": 0}

    def fake_extract(path: Path, **_kwargs):
        calls["count"] += 1
        return [("word/document.xml:1", "混凝土结构检测技术标准文本")]

    monkeypatch.setattr(knowledge_base, "extract_document", fake_extract)
    kb = knowledge_base.KnowledgeBase(tmp_path / "kb")
    first = kb.add_document(source)
    second = kb.add_document(source)
    assert first.status == "ready"
    assert second.status == "ready"
    assert second.document_id == first.document_id
    assert calls["count"] == 1


def test_docx_template_scan_and_render(tmp_path: Path) -> None:
    from runtime.document_templates import render_docx, scan_template

    template = tmp_path / "template.docx"
    xml = "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:p><w:r><w:t>{{title}}</w:t></w:r></w:p><w:p><w:r><w:t>{{#each findings}}{{name}} {{/each}}</w:t></w:r></w:p></w:document>"
    with zipfile.ZipFile(template, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    metadata = scan_template(template, allowed_fields={"title", "findings", "/each", "name"})
    assert metadata.status == "ready"
    output = tmp_path / "output.docx"
    render_docx(template, output, {"title": "损伤报告", "findings": [{"name": "裂缝"}]})
    with zipfile.ZipFile(output) as archive:
        rendered = archive.read("word/document.xml").decode("utf-8")
    assert "损伤报告" in rendered
    assert "裂缝" in rendered
    assert "{{title}}" not in rendered


def test_template_registration_copies_external_source_into_managed_directory(tmp_path: Path) -> None:
    from runtime.document_templates import register_managed_template

    source_dir = tmp_path / "external"
    source_dir.mkdir()
    source = source_dir / "report-template.docx"
    xml = "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>{{title}}</w:t></w:r></w:p></w:body></w:document>"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)

    managed_dir = tmp_path / "templates"
    metadata = register_managed_template(source, managed_dir)

    assert metadata.status == "ready"
    assert Path(metadata.path).parent == managed_dir.resolve()
    assert Path(metadata.path).is_file()
    assert source.is_file()


def test_knowledge_base_indexes_and_scopes_docx(tmp_path: Path) -> None:
    from runtime.knowledge_base import KnowledgeBase

    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"
    xml_template = "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    for path, text in ((first, "桥梁裂缝修复工法"), (second, "钢索腐蚀防护工法")):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", xml_template.format(text=text))
    kb = KnowledgeBase(tmp_path / "kb")
    root_folder = kb.create_folder("桥梁资料")
    child_folder = kb.create_folder("修复工法", parent_id=root_folder.folder_id)
    empty_folder = kb.create_folder("空分类")
    record_a = kb.add_document(first, folder_id=child_folder.folder_id)
    record_b = kb.add_document(second)
    assert record_a.status == record_b.status == "ready"
    assert Path(record_a.path).parent == (tmp_path / "kb" / "source_files").resolve()
    assert Path(record_b.path).parent == (tmp_path / "kb" / "source_files").resolve()
    assert first.is_file() and second.is_file()
    results = kb.search("裂缝", document_ids=[record_a.document_id], top_k=5, max_chars=1000)
    assert results and results[0].document_id == record_a.document_id
    assert kb.list_documents_in_folders([root_folder.folder_id])[0].document_id == record_a.document_id
    scoped = kb.search("裂缝", folder_ids=[root_folder.folder_id], top_k=5, max_chars=1000)
    assert scoped and scoped[0].document_id == record_a.document_id
    assert not kb.search("腐蚀", document_ids=[record_a.document_id], top_k=5, max_chars=1000)
    assert not kb.search("裂缝", folder_ids=[empty_folder.folder_id], top_k=5, max_chars=1000)
    kb.remove_document(record_a.document_id)
    assert not any(item.document_id == record_a.document_id for item in kb.search("裂缝", top_k=5, max_chars=1000))


def test_scoped_search_falls_back_without_crossing_profile_scope(tmp_path: Path) -> None:
    from runtime.knowledge_base import KnowledgeBase

    report_doc = tmp_path / "report.docx"
    plan_doc = tmp_path / "plan.docx"
    xml_template = "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    for path, text in ((report_doc, "建筑结构可靠性鉴定与混凝土检测条款"), (plan_doc, "混凝土裂缝修复施工与验收要求")):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", xml_template.format(text=text))
    kb = KnowledgeBase(tmp_path / "kb")
    report_folder = kb.create_folder("损伤分析")
    plan_folder = kb.create_folder("施工")
    report_record = kb.add_document(report_doc, folder_id=report_folder.folder_id)
    plan_record = kb.add_document(plan_doc, folder_id=plan_folder.folder_id)

    report_result = kb.search_with_scope(
        "完全不可能匹配的检测词",
        folder_ids=[report_folder.folder_id],
        top_k=3,
        max_chars=1000,
    )
    assert report_result.used_scoped_fallback is True
    assert report_result.scope_available is True
    assert {chunk.document_id for chunk in report_result.chunks} == {report_record.document_id}

    plan_result = kb.search_with_scope(
        "完全不可能匹配的施工词",
        folder_ids=[plan_folder.folder_id],
        top_k=3,
        max_chars=1000,
    )
    assert plan_result.used_scoped_fallback is True
    assert {chunk.document_id for chunk in plan_result.chunks} == {plan_record.document_id}

    empty_folder = kb.create_folder("空范围")
    empty_result = kb.search_with_scope("任意查询", folder_ids=[empty_folder.folder_id])
    assert empty_result.scope_available is False
    assert empty_result.chunks == ()
    invalid_result = kb.search_with_scope("任意查询", folder_ids=["missing-folder"])
    assert invalid_result.scope_available is False
    assert invalid_result.chunks == ()


def test_knowledge_base_folder_explorer_operations(tmp_path: Path) -> None:
    from runtime.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(tmp_path / "kb")
    root = kb.create_folder("简历")
    child = kb.create_folder("2026", parent_id=root.folder_id)
    renamed = kb.rename_folder(root.folder_id, "简历归档")
    assert renamed.relative_path == "简历归档"
    assert next(folder for folder in kb.list_folders() if folder.folder_id == child.folder_id).relative_path == "简历归档/2026"
    moved = kb.move_folder(child.folder_id, "")
    assert moved.relative_path == "2026"
    removed = kb.delete_folder(moved.folder_id)
    assert removed == []
    assert all(folder.folder_id != child.folder_id for folder in kb.list_folders())
    removed_docs = kb.delete_folder(root.folder_id)
    assert removed_docs == []
    assert not kb.list_folders()


def test_empty_knowledge_base_skips_large_structured_query(tmp_path: Path) -> None:
    from runtime.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(tmp_path / "kb")
    large_query = json.dumps({"results": [{"damage_findings": [{"class_name": "crack", "score": 0.9} for _ in range(300)]}]})
    assert kb.search(large_query, top_k=6, max_chars=10000) == []


def test_knowledge_base_bounds_large_query_token_count(tmp_path: Path) -> None:
    from runtime.knowledge_base import KnowledgeBase

    source = tmp_path / "reference.docx"
    xml = "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>Structural crack repair injection</w:t></w:r></w:p></w:body></w:document>"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    kb = KnowledgeBase(tmp_path / "kb")
    kb.add_document(source)
    noisy = " ".join(f"token_{index}" for index in range(2000)) + " structural crack"
    results = kb.search(noisy, top_k=6, max_chars=10000)
    assert isinstance(results, list)


def test_unified_settings_entry_and_profile_sections() -> None:
    import runtime

    source = Path(runtime.__file__).resolve().parent / "damage_workflow_gui.py"
    source_text = source.read_text(encoding="utf-8")
    for label in (
        "setWindowTitle(\"设置\")",
        "settingsNav",
        "QStackedWidget",
        "nav.addItems([\"API\", \"知识库\", \"损伤分析报告\", \"施工方案\", \"修复渲染图\"])",
        "QTreeWidget",
        "新建文件夹",
        "知识库范围",
        "profile_display_names",
        "损伤分析报告",
        "本功能输出经过校验的 JSON 和 Markdown；Word 模板仅保留为历史兼容数据。",
        "add_page(\"修复渲染图\", \"修复渲染图提示词\")",
        "修复渲染图提示词",
        "修复渲染平台",
        "硅基流动 API URL",
        "硅基流动 API Key",
        "硅基流动图像模型",
        "QTabBar::tab:selected",
    ):
        assert label in source_text
    assert "项目环境：YOLO11-HAI" not in source_text
    assert "造价预估" not in source_text
    assert "造價預估" not in source_text
    assert "DamageCostEstimator" not in source_text
    assert "repair_plan_cost.json" not in source_text
    assert 'QPushButton("上传 Word 模板")' not in source_text
    assert "def _open_settings_vertical" in source_text
    try:
        import runtime.damage_workflow_gui as gui
    except ImportError:
        return

    if gui.GUI_IMPORT_ERROR is not None:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = gui.DamageWorkflowWindow()
    assert window.api_button.text() == "设置"
    assert not window.api_button.icon().isNull()
    window.open_settings
    assert set(window.settings.generation_profiles) == {"損傷分析報告", "施工方案"}
    window.close()
