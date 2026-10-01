from __future__ import annotations

from runtime.simplified_chinese import simplify_chinese_text, simplify_chinese_value


def test_simplify_chinese_text_covers_engineering_terms() -> None:
    value = "僅在損傷身份、範圍、基材狀態與人工確認報告一致時適用"
    assert simplify_chinese_text(value) == "仅在损伤身份、范围、基材状态与人工确认报告一致时适用"


def test_simplify_chinese_value_preserves_identity_paths_and_markers() -> None:
    payload = {
        "image_name": "现场損傷圖.jpg",
        "original_image_path": r"C:\資料\現場圖.jpg",
        "source_marker": "[KB:doc-1:page:2]",
        "knowledge_references": ["[规范A 4.2]"],
        "description": "複核現場證據與適用條件",
    }
    normalized = simplify_chinese_value(payload)
    assert normalized["image_name"] == "现场損傷圖.jpg"
    assert normalized["original_image_path"] == r"C:\資料\現場圖.jpg"
    assert normalized["source_marker"] == "[KB:doc-1:page:2]"
    assert normalized["knowledge_references"] == ["[规范A 4.2]"]
    assert normalized["description"] == "复核现场证据与适用条件"
