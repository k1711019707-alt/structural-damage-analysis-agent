"""Shared evidence + knowledge context used by report/plan/estimate generators."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from runtime.knowledge_base import KnowledgeBaseChunk
from runtime.settings_models import GenerationProfile


@dataclass
class GenerationContext:
    profile_name: str
    profile_version: int
    settings_snapshot_id: str
    evidence: dict[str, Any]
    knowledge_base_used: bool
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    prompt: str = ""
    profile_prompt: str = ""
    knowledge_base_retrieval_mode: str = "none"
    knowledge_base_status: str = "not_used"
    knowledge_base_scope_document_ids: tuple[str, ...] = ()
    knowledge_base_scope_folder_ids: tuple[str, ...] = ()
    input_evidence_hash: str = ""
    created_at: str = ""
    anchors: list[dict[str, Any]] = field(default_factory=list)
    context_groups: list[dict[str, Any]] = field(default_factory=list)
    retrieval_warnings: list[str] = field(default_factory=list)
    evidence_groups: list[dict[str, Any]] = field(default_factory=list)
    answer_source_mode: str = "knowledge_base"
    external_fallback_reason: str = ""
    external_sources: list[dict[str, Any]] = field(default_factory=list)
    requires_source_citation: bool = False
    knowledge_freshness_warning: str = ""
    engineer_review_required: bool = True
    route_diagnostics: dict[str, Any] = field(default_factory=dict)

    def manifest(self, *, model: str, template_version: str = "") -> dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "profile_version": self.profile_version,
            "settings_snapshot_id": self.settings_snapshot_id,
            "knowledge_base_used": self.knowledge_base_used,
            "knowledge_base_retrieval_mode": self.knowledge_base_retrieval_mode,
            "knowledge_base_status": self.knowledge_base_status,
            "knowledge_base_scope_document_ids": list(self.knowledge_base_scope_document_ids),
            "knowledge_base_scope_folder_ids": list(self.knowledge_base_scope_folder_ids),
            "retrieved_chunks": [
                {key: value for key, value in chunk.items() if key != "text"}
                for chunk in self.retrieved_chunks
            ],
            "anchors": self.anchors,
            "context_groups": [
                {key: value for key, value in group.items() if key != "chunks"}
                for group in self.context_groups
            ],
            "retrieval_warnings": list(self.retrieval_warnings),
            "answer_source_mode": self.answer_source_mode,
            "external_fallback_reason": self.external_fallback_reason,
            "external_sources": [dict(item) for item in self.external_sources],
            "requires_source_citation": self.requires_source_citation,
            "knowledge_freshness_warning": self.knowledge_freshness_warning,
            "engineer_review_required": self.engineer_review_required,
            "route_diagnostics": dict(self.route_diagnostics),
            "evidence_groups": [
                {
                    "group_id": group.get("group_id", ""),
                    "anchor_chunk_ids": list(group.get("anchor_chunk_ids") or []),
                    "heading_path": list(group.get("heading_path") or []),
                    "items": [
                        {key: value for key, value in item.items() if key not in {"text", "metadata"}}
                        for item in (group.get("items") or [])
                    ],
                }
                for group in self.evidence_groups
            ],
            "input_evidence_hash": self.input_evidence_hash,
            "model": model,
            "template_version": template_version,
            "created_at": self.created_at,
        }


def normalize_json_evidence(value: Any, *, _path: str = "evidence") -> Any:
    """Convert supported evidence values to deterministic JSON-compatible data."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return normalize_json_evidence(model_dump(mode="json"), _path=_path)
    if is_dataclass(value) and not isinstance(value, type):
        return normalize_json_evidence(asdict(value), _path=_path)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): normalize_json_evidence(item, _path=f"{_path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            normalize_json_evidence(item, _path=f"{_path}[{index}]")
            for index, item in enumerate(value)
        ]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        f"Evidence serialization error at {_path}: unsupported type "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


def build_generation_context(
    profile: GenerationProfile,
    evidence: dict[str, Any],
    *,
    settings_snapshot_id: str,
    retrieved_chunks: Iterable[KnowledgeBaseChunk] = (),
    retrieval_mode: str = "lexical",
    knowledge_base_scope_document_ids: Iterable[str] = (),
    knowledge_base_scope_folder_ids: Iterable[str] | None = None,
    knowledge_base_status: str | None = None,
    output_constraints: str = "",
    anchors: Iterable[dict[str, Any]] = (),
    context_groups: Iterable[dict[str, Any]] = (),
    retrieval_warnings: Iterable[str] = (),
    answer_source_mode: str | None = None,
    external_fallback_reason: str = "",
    external_sources: Iterable[dict[str, Any]] = (),
    route_diagnostics: dict[str, Any] | None = None,
    retrieval_query: str = "",
    web_search_provider: Any | None = None,
) -> GenerationContext:
    normalized_evidence = normalize_json_evidence(evidence)
    if not isinstance(normalized_evidence, dict):
        raise TypeError("Evidence serialization error: top-level evidence must be a mapping")
    anchors_list = [dict(item) for item in anchors]
    groups_list = [dict(item) for item in context_groups]
    warnings_list = list(dict.fromkeys(str(item) for item in retrieval_warnings if str(item)))
    sources_list = [dict(item) for item in external_sources if isinstance(item, dict)]
    chunks = [
        {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "location": chunk.location,
            "source_marker": chunk.source_marker,
            "score": chunk.score,
            "metadata": dict(getattr(chunk, "metadata", {}) or {}),
            "text": chunk.text,
        }
        for chunk in retrieved_chunks
    ]
    evidence_json = json.dumps(
        normalized_evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence_hash = hashlib.sha256(evidence_json.encode()).hexdigest()
    query = str(retrieval_query or normalized_evidence.get("project_overview") or profile.name)
    kb_has_answer = bool(chunks and anchors_list and retrieval_mode != "scoped_fallback")
    source_mode = str(answer_source_mode or "").strip().lower()
    if source_mode not in {"knowledge_base", "web_search", "model_prior"}:
        from knowledge_pipeline.external_fallback import route_external_answer

        route = route_external_answer(
            query,
            knowledge_base_has_answer=kb_has_answer,
            web_search_provider=web_search_provider,
        )
        source_mode = route.answer_source_mode
        external_fallback_reason = route.reason
        sources_list = [dict(item) for item in route.sources]
        warnings_list.extend(route.warnings)
    from knowledge_pipeline.generate import build_generation_context as build_pipeline_generation_context

    project_overview = str(normalized_evidence.get("project_overview", "") or "")
    base_prompt_parts = [
        f"用户设置的生成提示词（唯一业务指令）：\n{profile.prompt.strip()}",
        f"项目概括：\n{project_overview}" if project_overview.strip() else "项目概括：无",
        f"输入数据：\n{evidence_json}",
        f"知识库检索模式：{retrieval_mode}",
    ]
    if str(output_constraints or "").strip():
        base_prompt_parts.append(str(output_constraints).strip())
    retrieved_payload = {
        "query": query,
        "chunks": chunks,
        "retrieval_mode": retrieval_mode,
        "relevance_status": "hit" if kb_has_answer else "unknown",
        "scope_available": bool(knowledge_base_scope_document_ids),
        "scope_document_ids": [str(item) for item in knowledge_base_scope_document_ids if str(item)],
        "anchors": anchors_list,
        "context_groups": groups_list,
        "status": {"status": "ready", "warnings": warnings_list, "stage_version": "retrieve.v2"},
    }
    canonical = build_pipeline_generation_context(
        query=query,
        evidence=normalized_evidence,
        retrieved=retrieved_payload,
        prompt="\n\n".join(base_prompt_parts),
        generation_mode="damage-grounded-summary",
        answer_source_mode=source_mode,
        external_fallback_reason=external_fallback_reason,
        external_sources=sources_list,
    )
    prompt = canonical.prompt
    resolved_knowledge_status = knowledge_base_status or ("used" if chunks else "not_used")
    return GenerationContext(
        profile_name=profile.name,
        profile_version=profile.version,
        settings_snapshot_id=settings_snapshot_id,
        evidence=normalized_evidence,
        knowledge_base_used=bool(chunks),
        retrieved_chunks=chunks,
        prompt=prompt,
        profile_prompt=profile.prompt.strip(),
        knowledge_base_retrieval_mode=(retrieval_mode if chunks else "none"),
        knowledge_base_status=resolved_knowledge_status,
        knowledge_base_scope_document_ids=tuple(str(item) for item in knowledge_base_scope_document_ids if str(item)),
        knowledge_base_scope_folder_ids=tuple(
            str(item)
            for item in (
                profile.knowledge_base_folder_ids
                if knowledge_base_scope_folder_ids is None
                else knowledge_base_scope_folder_ids
            )
            if str(item)
        ),
        input_evidence_hash=evidence_hash,
        created_at=datetime.now(timezone.utc).isoformat(),
        anchors=anchors_list,
        context_groups=groups_list,
        retrieval_warnings=list(canonical.status.warnings),
        evidence_groups=list(canonical.evidence_groups),
        answer_source_mode=canonical.answer_source_mode,
        external_fallback_reason=canonical.external_fallback_reason,
        external_sources=list(canonical.external_sources),
        requires_source_citation=canonical.requires_source_citation,
        knowledge_freshness_warning=canonical.knowledge_freshness_warning,
        engineer_review_required=canonical.engineer_review_required,
        route_diagnostics=dict(route_diagnostics or {}),
    )


def render_reference_appendix(context: GenerationContext, *, title: str) -> str:
    """Render a deterministic reference appendix for non-LLM plan/estimate outputs."""
    lines = [f"# {title}", "", "本文件保留结构化项目证据，并附上生成设置允许使用的知识库参考片段。", ""]
    if not context.retrieved_chunks:
        lines.extend(["## 知识库参考", "", "未启用知识库或没有匹配片段。", ""])
        return "\n".join(lines)
    lines.extend(["## 知识库参考", ""])
    if context.evidence_groups:
        from knowledge_pipeline.generate import render_evidence_groups

        lines.extend([render_evidence_groups(context.evidence_groups), ""])
    else:
        for chunk in context.retrieved_chunks:
            lines.extend([f"### {chunk['source_marker']}", "", chunk["text"], ""])
    return "\n".join(lines)
