"""Generation-context stage for report/plan callers."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from .contracts import GENERATION_SCHEMA_VERSION, GenerationContext, StageStatus, read_json, write_json


_DAMAGE_SUMMARY_CONSTRAINTS = """你正在生成结构损伤知识总结。只能依据提供的项目证据和知识库上下文作答，不得把推测写成规范要求。请按以下结构输出：
1. 损伤事实：只描述证据明确观察到的裂缝、剥落、变形、锈蚀等现象；
2. 可能原因：明确标注为可能性，不得冒充已证实原因；
3. 需要复核的指标：列出位置、宽度、长度、深度、发展性、构件受力和相关检测项目；
4. 风险判断：说明证据支持的风险和证据不足之处，不得仅凭外观直接宣布结构安全等级；
5. 建议措施：区分检测、临时防护和后续修复建议，并写明适用条件；
6. 限制与复核：涉及承重构件、节点、持续发展裂缝、明显变形或证据冲突时，明确要求专业检测或工程师复核；
7. 来源引用：每个重要结论尽量引用对应的 [KB:...] 来源标记。
"""

_EVIDENCE_TYPE_RULES = """知识库证据使用规则：
- 直接命中证据（anchor）优先于父级上下文和章节扩展；扩展内容只用于补全语境。
- 表格证据必须连同表头、单位、适用条件和来源理解，不得把单个单元格脱离表格解释。
- 图片 OCR 是图中可见文字的机器识别结果，可能存在错字、漏字或单位错误，重要数值必须复核原页。
- 标记为“模型生成视觉描述”的内容不是 PDF 原文、规范条款或检测结论，必须有其他证据印证后才能用于工程判断。
- needs_review 或存在质量标记的证据只能作为待复核依据；不得据此自动确定安全等级、材料参数或施工条件。
- 每个重要结论必须保留对应 [KB:...] 来源；没有证据支持时明确说明证据不足。
"""


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _evidence_role(item: dict[str, Any]) -> str:
    metadata = dict(item.get("metadata") or {})
    if metadata.get("anchor") or metadata.get("retrieval_channels"):
        return "anchor"
    if metadata.get("retrieval_role") == "context_only" or metadata.get("context_for"):
        return "parent_context"
    if metadata.get("expanded"):
        return "expanded_context"
    return "anchor" if metadata.get("retrieval_role", "retrieval") == "retrieval" else "context"


def _evidence_type(item: dict[str, Any]) -> str:
    metadata = dict(item.get("metadata") or {})
    kind = str(metadata.get("chunk_type") or "paragraph")
    if metadata.get("table_id") or kind in {"table", "table_row"}:
        return "table"
    if metadata.get("model_generated"):
        return "visual_generated"
    if kind == "image_ocr" or metadata.get("image_id") or metadata.get("region_id"):
        return "image_ocr"
    return "text"


def _descriptor(item: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    channels = [str(value) for value in (metadata.get("retrieval_channels") or []) if str(value)]
    return {
        "chunk_id": str(item.get("chunk_id") or ""),
        "parent_id": str(item.get("parent_id") or metadata.get("parent_chunk_id") or ""),
        "source_marker": str(item.get("source_marker") or ""),
        "role": _evidence_role(item),
        "evidence_type": _evidence_type(item),
        "retrieval_channels": channels,
        "needs_review": bool(metadata.get("needs_review") or metadata.get("model_generated") or metadata.get("quality_flags")),
        "model_generated": bool(metadata.get("model_generated")),
        "quality_flags": [str(value) for value in (metadata.get("quality_flags") or []) if str(value)],
        "table_id": str(metadata.get("table_id") or ""),
        "region_id": str(metadata.get("region_id") or ""),
        "text": str(item.get("text") or "").strip(),
        "included": True,
        "duplicate_of": "",
        "metadata": metadata,
    }


def build_evidence_groups(
    chunks: list[dict[str, Any]],
    *,
    anchors: list[dict[str, Any]] | None = None,
    context_groups: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Create prompt-safe anchor/context groups without dropping audit facts."""
    descriptors = [_descriptor(item) for item in chunks if isinstance(item, dict)]
    by_id = {item["chunk_id"]: item for item in descriptors if item["chunk_id"]}
    anchor_ids = [str(item.get("chunk_id") or "") for item in (anchors or []) if isinstance(item, dict) and item.get("chunk_id")]
    if not anchor_ids:
        anchor_ids = [item["chunk_id"] for item in descriptors if item["role"] == "anchor" and item["chunk_id"]]
    groups: list[dict[str, Any]] = []
    assigned: set[str] = set()
    relation_groups = [item for item in (context_groups or []) if isinstance(item, dict)]
    for group_index, relation in enumerate(relation_groups):
        ids = [str(value) for value in (relation.get("chunk_ids") or []) if str(value) in by_id]
        group_anchors = [str(value) for value in (relation.get("anchor_chunk_ids") or []) if str(value) in by_id]
        ordered = [*group_anchors, *(value for value in ids if value not in group_anchors)]
        items = [by_id[value] for value in ordered if value not in assigned]
        if items:
            assigned.update(item["chunk_id"] for item in items)
            groups.append({"group_id": f"evidence:{group_index + 1}", "anchor_chunk_ids": group_anchors, "heading_path": list(relation.get("heading_path") or []), "expansion_reason": str(relation.get("expansion_reason") or ""), "items": items})
    for anchor_id in anchor_ids:
        if anchor_id not in by_id or anchor_id in assigned:
            continue
        related = [by_id[anchor_id]]
        assigned.add(anchor_id)
        for item in descriptors:
            if item["chunk_id"] in assigned:
                continue
            metadata = item["metadata"]
            if metadata.get("context_for") == anchor_id or metadata.get("anchor_chunk_id") == anchor_id or (item["role"] == "parent_context" and item["chunk_id"] == by_id[anchor_id]["parent_id"]):
                related.append(item); assigned.add(item["chunk_id"])
        groups.append({"group_id": f"evidence:{len(groups) + 1}", "anchor_chunk_ids": [anchor_id], "heading_path": list(by_id[anchor_id]["metadata"].get("heading_path") or []), "expansion_reason": "parent_or_expanded_context", "items": related})
    for item in descriptors:
        if item["chunk_id"] not in assigned:
            groups.append({"group_id": f"evidence:{len(groups) + 1}", "anchor_chunk_ids": [item["chunk_id"]] if item["role"] == "anchor" else [], "heading_path": list(item["metadata"].get("heading_path") or []), "expansion_reason": "standalone", "items": [item]})

    seen: dict[str, str] = {}
    for group in groups:
        anchors_in_group = [item for item in group["items"] if item["role"] == "anchor"]
        for item in group["items"]:
            normalized = _clean_text(item["metadata"].get("text_raw") or item["text"])
            digest_key = normalized.casefold()
            if digest_key and digest_key in seen:
                item["included"] = False; item["duplicate_of"] = seen[digest_key]
                continue
            if item["role"] != "anchor" and normalized:
                for anchor in anchors_in_group:
                    anchor_text = _clean_text(anchor["metadata"].get("text_raw") or anchor["text"])
                    if anchor_text and anchor_text in normalized and len(normalized) <= max(len(anchor_text) + 80, int(len(anchor_text) * 1.25)):
                        item["included"] = False; item["duplicate_of"] = anchor["chunk_id"]
                        break
            if item["included"] and digest_key:
                seen[digest_key] = item["chunk_id"]
    return groups


def render_evidence_groups(groups: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    role_labels = {"anchor": "直接召回证据", "parent_context": "父级上下文", "expanded_context": "章节扩展上下文", "context": "补充上下文"}
    type_labels = {"text": "正文", "table": "表格", "image_ocr": "图片OCR", "visual_generated": "模型生成视觉描述"}
    for index, group in enumerate(groups, 1):
        lines.append(f"### 证据组 {index}")
        if group.get("heading_path"):
            lines.append(f"章节：{' > '.join(map(str, group['heading_path']))}")
        for item in group.get("items") or []:
            if not item.get("included"):
                lines.append(f"- 重复上下文已省略：{item.get('source_marker') or item.get('chunk_id')}（与 {item.get('duplicate_of')} 重复）")
                continue
            channels = "+".join(item.get("retrieval_channels") or []) or "上下文补全"
            review = "；需要复核" if item.get("needs_review") else ""
            generated = "；非原文的模型描述" if item.get("model_generated") else ""
            lines.append(f"[{role_labels.get(item.get('role'), item.get('role'))}｜{type_labels.get(item.get('evidence_type'), item.get('evidence_type'))}｜通道:{channels}{review}{generated}]")
            lines.append(str(item.get("source_marker") or "[KB:来源缺失]"))
            lines.append(str(item.get("text") or ""))
        lines.append("")
    return "\n".join(lines).strip()


def build_generation_context(
    *,
    query: str,
    evidence: dict[str, Any] | None = None,
    retrieved: dict[str, Any] | None = None,
    prompt: str = "",
    generation_mode: str = "context-only",
    answer_source_mode: str | None = None,
    external_fallback_reason: str = "",
    external_sources: list[dict[str, Any]] | None = None,
) -> GenerationContext:
    retrieved = retrieved or {}
    chunks = list(retrieved.get("chunks") or [])
    retrieval_status = retrieved.get("status") or {}
    retrieval_warnings = [str(item) for item in (retrieval_status.get("warnings") or []) if str(item)] if isinstance(retrieval_status, dict) else []
    anchors = [dict(item) for item in (retrieved.get("anchors") or []) if isinstance(item, dict)]
    context_groups = [dict(item) for item in (retrieved.get("context_groups") or []) if isinstance(item, dict)]
    evidence_groups = build_evidence_groups(chunks, anchors=anchors, context_groups=context_groups)
    rendered_evidence = render_evidence_groups(evidence_groups)
    retrieval_mode = str(retrieved.get("retrieval_mode") or "unknown")
    relevance_status = str(retrieved.get("relevance_status") or "unknown")
    scope_available = bool(retrieved.get("scope_available", False))
    scope_document_ids = [str(item) for item in (retrieved.get("scope_document_ids") or []) if str(item)]
    requested_source_mode = str(answer_source_mode or retrieved.get("answer_source_mode") or "").strip().lower()
    sources = [dict(item) for item in (external_sources if external_sources is not None else retrieved.get("external_sources") or []) if isinstance(item, dict)]
    if requested_source_mode not in {"knowledge_base", "web_search", "model_prior"}:
        requested_source_mode = "knowledge_base" if chunks and relevance_status == "hit" else ("web_search" if sources else "model_prior")
    if requested_source_mode == "web_search" and not sources:
        requested_source_mode = "model_prior"
        external_fallback_reason = external_fallback_reason or "web_search_unverified_or_unavailable"
    if not chunks and not external_fallback_reason:
        external_fallback_reason = "knowledge_base_no_sufficient_evidence"
    freshness_warning = ""
    source_instructions = ""
    if requested_source_mode == "web_search":
        freshness_warning = "联网来源必须保留可核验 URL、标题和访问时间；不得将网页来源写成 [KB:...]。"
        source_instructions = "当前回答来源：web_search。只能使用随上下文提供且可核验的外部来源，并在结论后标注外部来源；不得伪造网页或知识库引用。"
    elif requested_source_mode == "model_prior":
        freshness_warning = "以下内容不是当前知识库证据，可能过时或不完整，必须结合最新资料复核。"
        source_instructions = "当前回答来源：model_prior。明确声明内容来自模型一般知识而非当前知识库证据；不得生成 [KB:...] 伪引用，工程结论必须人工复核。"
    else:
        source_instructions = "当前回答来源：knowledge_base。重要结论应引用对应 [KB:...] 来源标记。"
    context_prompt = "\n\n".join(item for item in [prompt.strip(), f"检索模式：{retrieval_mode}；相关性状态：{relevance_status}", f"回答来源模式：{requested_source_mode}", source_instructions, freshness_warning, f"知识库结构化证据：\n{rendered_evidence}" if rendered_evidence else "知识库结构化证据：无", _EVIDENCE_TYPE_RULES] if item)
    warnings = list(retrieval_warnings) + ([] if chunks else ["未提供知识库召回结果"])
    if retrieval_mode == "scoped_fallback" or relevance_status in {"unknown", "unavailable"}:
        warnings.append("检索结果不是已确认的相关性命中，只能作为范围内参考，不得作为确定性结论依据")
    if requested_source_mode == "model_prior":
        warnings.extend(["answer_source_mode:model_prior", "非知识库证据：可能过时，请复核", "工程高风险内容需要工程师复核"])
    elif requested_source_mode == "web_search":
        warnings.append("answer_source_mode:web_search；外部来源与知识库证据分开记录")
    if generation_mode == "damage-grounded-summary":
        context_prompt = "\n\n".join(item for item in [context_prompt, _DAMAGE_SUMMARY_CONSTRAINTS] if item)
        if not chunks:
            warnings.append("损伤总结缺少可引用的知识库证据")
        elif any(bool((item.get("metadata") or {}).get("needs_review")) for item in chunks):
            warnings.append("上下文包含 needs_review 证据，生成结果必须保留复核边界")
    return GenerationContext(
        query=query,
        evidence=dict(evidence or {}),
        retrieved_chunks=chunks,
        prompt=context_prompt,
        generation_mode=generation_mode,
        review_status="pending_engineer_review",
        status=StageStatus(status="ready", stage_version="generate.v2", warnings=list(dict.fromkeys(warnings))),
        retrieval_mode=retrieval_mode,
        relevance_status=relevance_status,
        scope_available=scope_available,
        scope_document_ids=scope_document_ids,
        retrieval_warnings=retrieval_warnings,
        anchors=anchors,
        context_groups=context_groups,
        evidence_groups=evidence_groups,
        answer_source_mode=requested_source_mode,
        external_fallback_reason=external_fallback_reason,
        external_sources=sources,
        requires_source_citation=requested_source_mode in {"knowledge_base", "web_search"},
        knowledge_freshness_warning=freshness_warning,
        engineer_review_required=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build auditable generation context")
    parser.add_argument("retrieval", type=Path); parser.add_argument("output", type=Path)
    parser.add_argument("--query", default=""); parser.add_argument("--evidence", type=Path); parser.add_argument("--prompt", default="")
    parser.add_argument("--generation-mode", default="context-only", choices=["context-only", "damage-grounded-summary"])
    args = parser.parse_args(argv)
    evidence = read_json(str(args.evidence)) if args.evidence else {}
    retrieval = read_json(str(args.retrieval))
    query = args.query or str(retrieval.get("query", ""))
    write_json(str(args.output), build_generation_context(query=query, evidence=evidence, retrieved=retrieval, prompt=args.prompt, generation_mode=args.generation_mode).to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
