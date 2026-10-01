"""Safe routing for answers that are not supported by the active KB."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class WebSearchProvider(Protocol):
    def search(self, query: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ExternalAnswerRoute:
    answer_source_mode: str
    reason: str
    sources: tuple[dict[str, Any], ...] = ()
    requires_source_citation: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_source_mode": self.answer_source_mode,
            "external_fallback_reason": self.reason,
            "external_sources": [dict(item) for item in self.sources],
            "requires_source_citation": self.requires_source_citation,
            "warnings": list(self.warnings),
        }


def _verified_sources(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, (list, tuple)):
        return []
    verified: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            to_source = getattr(item, "to_source", None)
            model_dump = getattr(item, "model_dump", None)
            if callable(to_source):
                item = to_source()
            elif callable(model_dump):
                item = model_dump()
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        title = str(item.get("title") or item.get("name") or "").strip()
        if not url or not title or not (url.startswith("https://") or url.startswith("http://")):
            continue
        copied = dict(item)
        copied["url"] = url
        copied["title"] = title
        copied.setdefault("accessed_at", "")
        copied["source_type"] = "web"
        copied["source_ref"] = f"[WEB:{url}]"
        verified.append(copied)
    return verified


def route_external_answer(
    query: str,
    *,
    knowledge_base_has_answer: bool,
    web_search_provider: WebSearchProvider | Callable[[str], Any] | None = None,
) -> ExternalAnswerRoute:
    """Prefer verified web search; otherwise explicitly use model prior.

    The function never reports web_search unless at least one result has a URL
    and title. It does not inspect API keys or manufacture external sources.
    """
    if knowledge_base_has_answer:
        return ExternalAnswerRoute("knowledge_base", "knowledge_base_evidence_sufficient")
    if web_search_provider is not None:
        try:
            raw = web_search_provider.search(query) if hasattr(web_search_provider, "search") else web_search_provider(query)
            sources = _verified_sources(raw)
            if sources:
                return ExternalAnswerRoute(
                    "web_search",
                    "knowledge_base_no_sufficient_evidence",
                    tuple(sources),
                    True,
                    ("external_sources_are_not_knowledge_base_citations",),
                )
        except Exception as exc:
            reason = f"web_search_failed:{type(exc).__name__}"
        else:
            reason = "web_search_unverified_or_empty"
    else:
        reason = "web_search_unavailable"
    return ExternalAnswerRoute(
        "model_prior",
        reason,
        (),
        False,
        (
            "非知识库证据：以下内容来自模型一般知识",
            "可能过时或不完整，请结合最新资料复核",
            "工程高风险内容需要工程师复核",
        ),
    )
