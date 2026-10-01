from __future__ import annotations

from knowledge_pipeline.external_fallback import route_external_answer
from knowledge_pipeline.retrieve import classify_query


def test_query_routes_are_deterministic_and_text_only() -> None:
    assert classify_query("第5.3.2条的裂缝限值").query_type == "exact_identifier"
    assert classify_query("表中钢筋保护层厚度对应值").query_type == "table_query"
    assert classify_query("裂缝宽度允许范围是多少 mm").query_type == "numeric_unit"
    assert classify_query("如果发现持续发展裂缝，什么条件下需要加固").query_type == "risk_condition"
    assert classify_query("现场发现斜向裂缝应该如何判断和处理").query_type == "natural_semantic"


def test_external_route_requires_verifiable_web_sources() -> None:
    class Provider:
        def search(self, query: str):
            return [{"title": "规范页面", "url": "https://example.com/spec", "accessed_at": "2026-09-18"}]

    result = route_external_answer("当前规范要求", knowledge_base_has_answer=False, web_search_provider=Provider())
    assert result.answer_source_mode == "web_search"
    assert result.sources[0]["url"].startswith("https://")

    unavailable = route_external_answer("当前规范要求", knowledge_base_has_answer=False)
    assert unavailable.answer_source_mode == "model_prior"
    assert any("非知识库证据" in warning for warning in unavailable.warnings)


def test_external_sources_cannot_be_kb_citations() -> None:
    result = route_external_answer(
        "当前规范要求",
        knowledge_base_has_answer=False,
        web_search_provider=lambda query: [{"title": "bad", "url": "not-a-url"}],
    )
    assert result.answer_source_mode == "model_prior"
