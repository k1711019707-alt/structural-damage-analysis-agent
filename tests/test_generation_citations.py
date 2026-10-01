from __future__ import annotations


def test_catalog_deduplicates_markers_and_hides_windows_source_paths() -> None:
    from runtime.generation_citations import build_citation_catalog

    marker = "[KB:00474f7b00ed539d4702:page:31]"
    chunks = [
        {
            "document_id": "00474f7b00ed539d4702",
            "location": "unit:157",
            "source_marker": marker,
            "metadata": {
                "source_name": (
                    "C:\\Users\\engineer\\AppData\\Local\\knowledge_base\\source_files\\"
                    "GB 50010-2010 混凝土结构设计规范-上.pdf"
                )
            },
        },
        {
            "document_id": "00474f7b00ed539d4702",
            "location": "unit:158",
            "source_marker": marker,
            "metadata": {
                "source_name": "GB 50010-2010 混凝土结构设计规范-上.pdf"
            },
        },
    ]

    catalog = build_citation_catalog(chunks, indexed_source_names={})

    assert catalog == [
        {
            "source_marker": marker,
            "document_id": "00474f7b00ed539d4702",
            "source_name": "GB 50010-2010 混凝土结构设计规范-上.pdf",
            "location": "page:31",
        }
    ]
    assert "Users" not in str(catalog)


def test_catalog_accepts_index_fallback_and_hides_posix_source_paths() -> None:
    from runtime.generation_citations import build_citation_catalog

    marker = "[KB:doc-2:page:42]"
    catalog = build_citation_catalog(
        [{
            "document_id": "doc-2",
            "location": "unit:171",
            "source_marker": marker,
            "metadata": {},
        }],
        indexed_source_names={"doc-2": "/srv/private/标准资料.pdf"},
    )

    assert catalog[0]["source_name"] == "标准资料.pdf"
    assert "/srv/private" not in str(catalog)


def test_renderer_preserves_audit_marker_and_labels_unknown_sources() -> None:
    from runtime.generation_citations import render_knowledge_citations

    known = "[KB:00474f7b00ed539d4702:page:31]"
    unknown = "[KB:unknown:page:9]"
    rendered = render_knowledge_citations(
        f"依据 {known}，并复核 {unknown}。",
        [{
            "source_marker": known,
            "document_id": "00474f7b00ed539d4702",
            "source_name": "GB 50010-2010 混凝土结构设计规范-上.pdf",
            "location": "page:31",
        }],
    )

    assert "来源：GB 50010-2010 混凝土结构设计规范-上.pdf，第31页" in rendered
    assert known in rendered
    assert f"来源未解析；内部标识：{unknown}" in rendered
