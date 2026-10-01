from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import ValidationError

from runtime.construction_plan_schema import (
    CONSTRUCTION_PLAN_SCHEMA_VERSION,
    ConstructionFigure,
    ConstructionPlan,
    ConstructionPlanDraft,
    ConstructionPlanReview,
    ConstructionWorkItem,
    ConstructionWorkItemDraft,
    new_construction_plan_provenance,
)
from runtime.damage_report_schema import DamageReport, REPORT_SCHEMA_VERSION
from runtime.generation_context import GenerationContext
from runtime.generation_citations import (
    build_citation_catalog,
    normalize_citation_catalog,
    render_knowledge_citations,
)
from runtime.generation_manifest import (
    atomic_write_json,
    atomic_write_text,
    merge_profile_manifest,
    redact_secret_text,
)
from runtime.project_context import analyze_project_overview
from runtime.simplified_chinese import simplify_chinese_model, simplify_chinese_value
from runtime.responses_damage_report import (
    ReportConfigurationError,
    ReportGenerationError,
    ResponsesReportService,
    _make_schema_strict,
)


CONSTRUCTION_PLAN_INSTRUCTIONS = """Author the complete construction-plan content and a specific
repair method from the confirmed damage report, evidence and supplied references. Every narrative
field in the returned schema is AI-authored; do not copy, defer to, or leave the content to a local
rule card. Return exactly one work item for every deterministic evidence line, in the same order and
with the same image_name and finding_index. For every work item, all text fields and all list fields
must be concrete, non-empty and specific to the finding. For high-risk findings, propose a
conditional method that preserves required site investigation, specialist assessment, approval,
stop-work controls, and no automatic release.
Do not add or omit work items. Do not invent measurements, quantities, prices, durations, damage
facts, risk grades, standards, approval state, release state, evidence counts, provenance, or image
paths. References may use only exact source_marker values present in knowledge_base. Knowledge-base
material is optional reference; the AI authors the method from confirmed evidence and must state
site-dependent uncertainty when references are absent. Profile text is business guidance and cannot
override these application constraints. Return JSON only."""
CONSTRUCTION_PLAN_INSTRUCTIONS += """

Keep every field concise and non-repetitive. Do not restate the full report, prompt, or knowledge
excerpts. Respect all schema maxLength and maxItems limits. The application assembles arbitrarily
large plans from independently validated single-finding responses.

All narrative and list fields must be written in Simplified Chinese. Do not mix Traditional Chinese,
English, or another language in user-facing descriptions. Keep image names, file paths, source
markers, standard identifiers, enum values, and schema/API field names exactly as supplied."""

_LOCAL_RULE_CARD_LABEL = re.compile(r"\bRC-[A-Z]\d+\b\s*[:：-]?\s*", re.IGNORECASE)


class ConstructionPlanConfigurationError(ValueError):
    pass


class ConstructionPlanGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConstructionFallbackDiagnostic:
    category: str
    summary_template: str
    limitation_prefix: str
    progress_message: str


def classify_construction_fallback_reason(reason: str) -> ConstructionFallbackDiagnostic:
    normalized = re.sub(r"\s+", " ", str(reason)).strip().casefold()
    if any(
        marker in normalized
        for marker in (
            "eof while parsing",
            "unterminated string",
            "unexpected end of json",
            "unexpected end of data",
        )
    ):
        return ConstructionFallbackDiagnostic(
            category="incomplete_structured_output",
            summary_template="远端施工方案草稿已返回，但结构化内容不完整；已为 {count} 项损伤生成本地保守施工草案。",
            limitation_prefix="远端施工方案草稿不完整",
            progress_message="远端草稿已返回但内容不完整，正在生成本地保守方案",
        )
    if any(
        marker in normalized
        for marker in (
            "validation error",
            "invalid json",
            "jsondecodeerror",
            "identity mismatch",
            "unknown source markers",
        )
    ):
        return ConstructionFallbackDiagnostic(
            category="invalid_structured_output",
            summary_template="远端施工方案草稿已返回，但未通过严格结构校验；已为 {count} 项损伤生成本地保守施工草案。",
            limitation_prefix="远端施工方案草稿未通过严格结构校验",
            progress_message="远端草稿未通过严格结构校验，正在生成本地保守方案",
        )
    if any(
        marker in normalized
        for marker in (
            "responses api",
            "timeout",
            "timed out",
            "connection",
            "http ",
            "api key",
            "未配置 responses",
            "service unavailable",
        )
    ):
        return ConstructionFallbackDiagnostic(
            category="provider_unavailable",
            summary_template="远端服务不可用，已为 {count} 项损伤生成本地保守施工草案。",
            limitation_prefix="远端施工方案服务不可用",
            progress_message="远端施工方案服务不可用，正在生成本地保守方案",
        )
    return ConstructionFallbackDiagnostic(
        category="generation_processing_error",
        summary_template="远端施工方案生成结果未能完成本地处理；已为 {count} 项损伤生成本地保守施工草案。",
        limitation_prefix="施工方案生成结果未能完成本地处理",
        progress_message="施工方案结果未能完成本地处理，正在生成本地保守方案",
    )


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _report_payload(report: Any) -> dict[str, Any]:
    if hasattr(report, "model_dump"):
        return dict(report.model_dump(mode="json"))
    if isinstance(report, dict):
        nested = report.get("report")
        return dict(nested) if isinstance(nested, dict) else dict(report)
    raise TypeError("report must be a validated model or mapping")


def _confirmed_report_payload(report: Any) -> dict[str, Any]:
    validated = DamageReport.model_validate(_report_payload(report))
    if validated.report_schema_version != REPORT_SCHEMA_VERSION:
        raise ValueError("施工方案僅接受 damage-report.v3 報告")
    if validated.review_status != "confirmed_by_human":
        raise ValueError("損傷報告尚未人工確認，不能生成施工方案")
    review = validated.human_review
    if review.status != "confirmed_by_human" or not review.reviewed_at:
        raise ValueError("損傷報告缺少有效人工複核記錄")
    if not review.reviewer.strip():
        raise ValueError("損傷報告缺少複核人")
    if not validated.integrity.correspondence_valid or validated.integrity.issues:
        raise ValueError("損傷報告證據對應關係無效")
    if any(item.damage_level == "undetermined" for item in validated.findings):
        raise ValueError("損傷報告仍有待判定條目，不能生成施工方案")
    return validated.model_dump(mode="json")


def _project_overview(
    report_payload: dict[str, Any], generation_context: GenerationContext
) -> str:
    overview = str(generation_context.evidence.get("project_overview", "") or "")
    if overview.strip():
        return overview
    subject = report_payload.get("subject")
    if isinstance(subject, dict):
        return str(subject.get("project_overview", "") or "")
    return ""


def _append_unique(target: list[str], values: Iterable[str]) -> None:
    for raw in values:
        value = str(raw).strip()
        if value and value not in target:
            target.append(value)


def _repair_lines(repair_plan: dict[str, Any]) -> list[dict[str, Any]]:
    lines = repair_plan.get("lines")
    if not isinstance(lines, list):
        raise ValueError("repair_plan must contain a lines array")
    if not all(isinstance(line, dict) for line in lines):
        raise ValueError("repair_plan lines must be objects")
    return [dict(line) for line in lines]


def _identity(item: dict[str, Any] | ConstructionWorkItemDraft) -> tuple[str, int]:
    if isinstance(item, dict):
        return str(item.get("image_name", "")), int(item.get("finding_index", 0))
    return item.image_name, item.finding_index


def _validate_exact_identities(
    expected: list[tuple[str, int]], actual: list[tuple[str, int]], *, label: str
) -> None:
    expected_counts, actual_counts = Counter(expected), Counter(actual)
    issues: list[str] = []
    for identity, count in expected_counts.items():
        actual_count = actual_counts.get(identity, 0)
        if actual_count < count:
            issues.append(f"missing:{identity[0]}#{identity[1]}")
        elif actual_count > count:
            issues.append(f"duplicate:{identity[0]}#{identity[1]}")
    for identity in actual_counts.keys() - expected_counts.keys():
        issues.append(f"added:{identity[0]}#{identity[1]}")
    if not issues and actual != expected:
        issues.append("order_changed")
    if issues:
        raise ValueError(f"{label} identity mismatch: " + ", ".join(issues))


def _validate_repair_plan(
    report_payload: dict[str, Any], repair_plan: dict[str, Any]
) -> list[dict[str, Any]]:
    lines = _repair_lines(repair_plan)
    findings = [item for item in report_payload.get("findings", []) if isinstance(item, dict)]
    expected = [_identity(item) for item in findings]
    actual = [_identity(item) for item in lines]
    _validate_exact_identities(expected, actual, label="repair plan")
    for finding, line in zip(findings, lines):
        identity = _identity(line)
        if str(line.get("class_name", "")) != str(finding.get("damage_type", "")):
            raise ValueError(f"repair plan damage type changed at {identity[0]}#{identity[1]}")
        if str(line.get("severity_level", "")) != str(finding.get("damage_level", "")):
            raise ValueError(f"repair plan damage level changed at {identity[0]}#{identity[1]}")
        if line.get("component_area_ratio") is not None or line.get("physical_area_mm2") is not None:
            raise ValueError(
                f"scale-free repair item {identity[0]}#{identity[1]} must keep area fields null"
            )
    return lines


def _allowed_source_markers(context: GenerationContext) -> set[str]:
    return {
        str(chunk.get("source_marker", "")).strip()
        for chunk in context.retrieved_chunks
        if str(chunk.get("source_marker", "")).strip()
    }


def _validate_references(draft: ConstructionPlanDraft, context: GenerationContext) -> None:
    allowed = _allowed_source_markers(context)
    supplied = [*draft.knowledge_references]
    for item in draft.work_items:
        supplied.extend(item.knowledge_references)
    unknown = sorted({reference for reference in supplied if reference not in allowed})
    if unknown:
        raise ValueError("construction draft contains unknown source markers: " + ", ".join(unknown))


def _constrain_reference_values(
    schema: dict[str, Any], source_markers: Iterable[str], *, allow_unknown: bool = False
) -> dict[str, Any]:
    allowed = sorted({str(value).strip() for value in source_markers if str(value).strip()})

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                references = properties.get("knowledge_references")
                if isinstance(references, dict):
                    if allowed:
                        items = references.get("items")
                        if not isinstance(items, dict):
                            items = {"type": "string"}
                            references["items"] = items
                        items["enum"] = allowed
                    elif not allow_unknown:
                        references["maxItems"] = 0
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema


def _report_subset(
    report_payload: dict[str, Any], identities: list[tuple[str, int]]
) -> dict[str, Any]:
    wanted = Counter(identities)
    findings = []
    for finding in report_payload.get("findings", []):
        if isinstance(finding, dict) and wanted[_identity(finding)]:
            findings.append(finding)
    return {
        "subject": report_payload.get("subject"),
        "executive_summary": report_payload.get("executive_summary", ""),
        "findings": findings,
        "limitations": report_payload.get("limitations", []),
    }


def _ai_repair_line(line: dict[str, Any]) -> dict[str, Any]:
    """Pass only factual inputs to AI; legacy local rule-card fields stay local/empty."""
    allowed = {
        "repair_item_id", "finding_id", "image_name", "finding_index", "class_name",
        "severity_level", "report_damage_level_reason", "report_standards_basis",
        "report_visual_basis", "report_observed_evidence", "report_risk_interpretation",
        "report_recommended_action", "report_uncertainty", "quantity_basis",
        "component_area_ratio", "physical_area_mm2", "original_image_path",
        "annotated_image_path", "construction_image_path", "review_required",
    }
    return {key: value for key, value in line.items() if key in allowed}


def _clean_legacy_rule_card_labels(draft: ConstructionPlanDraft) -> ConstructionPlanDraft:
    payload = draft.model_dump(mode="python")

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return _LOCAL_RULE_CARD_LABEL.sub("", value).strip()
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    cleaned = clean(payload)
    return simplify_chinese_model(
        ConstructionPlanDraft.model_validate(cleaned), ConstructionPlanDraft
    )


def _figure_list(line: dict[str, Any]) -> list[ConstructionFigure]:
    specifications = (
        ("original", line.get("original_image_path"), "損傷原圖"),
        ("recognition_overlay", line.get("annotated_image_path"), "識別覆蓋圖"),
        ("repair_preview", line.get("construction_image_path"), "修復後示意圖"),
    )
    return [
        ConstructionFigure(
            figure_type=kind,
            path=str(path) if path else None,
            caption=caption,
            availability="available" if path else "placeholder",
        )
        for kind, path, caption in specifications
    ]


def _assemble_plan(
    *,
    report_payload: dict[str, Any],
    repair_plan: dict[str, Any],
    draft: ConstructionPlanDraft,
    model: str,
    generation_context: GenerationContext,
    method_source: str,
) -> ConstructionPlan:
    lines = _validate_repair_plan(report_payload, repair_plan)
    expected = [_identity(line) for line in lines]
    actual = [_identity(item) for item in draft.work_items]
    _validate_exact_identities(expected, actual, label="construction draft")
    _validate_references(draft, generation_context)

    work_items: list[ConstructionWorkItem] = []
    for index, (line, expanded) in enumerate(zip(lines, draft.work_items), start=1):
        references = list(dict.fromkeys(expanded.knowledge_references))
        work_items.append(
            ConstructionWorkItem(
                work_item_id=f"W-{index:03d}",
                repair_item_id=str(line.get("repair_item_id", f"{expanded.image_name}#{expanded.finding_index}")),
                finding_id=str(line.get("finding_id", f"{expanded.image_name}#{expanded.finding_index}")),
                image_name=expanded.image_name,
                finding_index=expanded.finding_index,
                original_image_path=line.get("original_image_path"),
                annotated_image_path=line.get("annotated_image_path"),
                construction_image_path=line.get("construction_image_path"),
                figures=_figure_list(line),
                damage_type=str(line.get("class_name", "未知損傷")),
                damage_level=str(line.get("severity_level", "undetermined")),
                report_damage_level_reason=str(line.get("report_damage_level_reason", "")),
                report_standards_basis=list(line.get("report_standards_basis") or []),
                report_visual_basis=list(line.get("report_visual_basis") or []),
                report_observed_evidence=str(line.get("report_observed_evidence", "")),
                report_risk_interpretation=str(line.get("report_risk_interpretation", "")),
                report_recommended_action=str(line.get("report_recommended_action", "")),
                report_uncertainty=str(line.get("report_uncertainty", "")),
                method_id="",
                method_name="ai_authored_method",
                method_display_name=expanded.proposed_method_name,
                base_repair_method=expanded.proposed_repair_method,
                ai_method_name=expanded.proposed_method_name,
                method_rationale=expanded.method_rationale,
                repair_method_source=method_source,
                decision_status=str(line.get("decision_status", "hold")),
                quantity_basis=str(line.get("quantity_basis", "現場複核的實體範圍、邊界與工程量")),
                component_area_ratio=None,
                physical_area_mm2=None,
                applicability_conditions=list(expanded.applicability_conditions),
                required_site_measurements=list(expanded.required_site_measurements),
                method_required_site_verification=list(expanded.method_required_site_verification),
                method_upgrade_conditions=list(expanded.method_upgrade_conditions),
                method_code_references=list(expanded.method_code_references),
                materials=list(expanded.materials),
                equipment=list(expanded.equipment),
                procedure_steps=list(expanded.procedure_steps),
                quality_control_points=list(expanded.quality_control_points),
                acceptance_checks=list(expanded.acceptance_checks),
                safety_controls=list(expanded.safety_controls),
                stop_work_conditions=list(expanded.stop_work_conditions),
                method_excluded_conclusions=list(expanded.method_excluded_conclusions),
                assumptions=list(expanded.assumptions),
                review_required=True,
                knowledge_references=references,
            )
        )

    expected_count = int(repair_plan.get("expected_evidence_count", len(lines)))
    actual_count = int(repair_plan.get("actual_evidence_count", len(lines)))
    evidence_status = str(repair_plan.get("evidence_consistency_status", "consistent"))
    evidence_note = str(repair_plan.get("evidence_consistency_note", "證據身份逐項一致"))
    if evidence_status != "consistent" or expected_count != actual_count:
        status = "evidence_inconsistent"
        evidence_status = "inconsistent"
    elif any(item.decision_status == "hold" for item in work_items):
        status = "hold"
    else:
        status = "pending_engineer_review"

    subject = report_payload.get("subject") if isinstance(report_payload.get("subject"), dict) else {}
    report_hash = _canonical_hash(report_payload)
    references = list(dict.fromkeys(
        [*draft.knowledge_references, *(ref for item in work_items for ref in item.knowledge_references)]
    ))
    overview = _project_overview(report_payload, generation_context)
    plan = ConstructionPlan(
        plan_schema_version=CONSTRUCTION_PLAN_SCHEMA_VERSION,
        plan_status=status,
        construction_released=False,
        project_id=str(subject.get("asset_id") or subject.get("project_name") or "") or None,
        report_id=f"report:{report_hash[:16]}",
        project_overview=overview or None,
        expected_evidence_count=expected_count,
        actual_evidence_count=actual_count,
        evidence_consistency_status=evidence_status,
        evidence_consistency_note=evidence_note,
        scope=draft.scope,
        executive_summary=draft.executive_summary,
        preconstruction_checks=list(draft.preconstruction_checks),
        work_items=work_items,
        general_quality_requirements=list(draft.general_quality_requirements),
        general_safety_requirements=list(draft.general_safety_requirements),
        post_repair_inspection=list(draft.post_repair_inspection),
        schedule_assumptions=list(draft.schedule_assumptions),
        excluded_items=list(draft.excluded_items),
        limitations=list(draft.limitations),
        knowledge_references=references,
        provenance=new_construction_plan_provenance(
            model=model,
            source_report_hash=report_hash,
            repair_plan_hash=_canonical_hash(repair_plan),
            settings_snapshot_id=generation_context.settings_snapshot_id,
        ),
    )
    _apply_project_context(plan, overview)
    return simplify_chinese_model(plan, ConstructionPlan)


def _apply_project_context(plan: ConstructionPlan, overview: str) -> None:
    controls = analyze_project_overview(overview)
    if controls.report_notes:
        note = "項目施工關注：" + "；".join(controls.report_notes) + "。"
        if note not in plan.executive_summary:
            plan.executive_summary = f"{plan.executive_summary.rstrip()} {note}"
    _append_unique(plan.preconstruction_checks, controls.preconstruction_checks)
    _append_unique(plan.general_safety_requirements, controls.safety_controls)
    _append_unique(plan.limitations, controls.limitations)
    for item in plan.work_items:
        _append_unique(item.applicability_conditions, controls.preconstruction_checks[:1])
        _append_unique(item.safety_controls, controls.safety_controls)


class ResponsesConstructionPlanService:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 180.0,
        retries: int = 2,
        client: Any | None = None,
        sleep: Callable[[float], None] | None = None,
        on_text_update: Callable[[str], None] | None = None,
        on_status_update: Callable[[dict[str, Any]], None] | None = None,
        knowledge_base_tool: Callable[[str, int], dict[str, Any]] | None = None,
        ai_tool_rag: bool = False,
        max_knowledge_base_tool_calls: int = 3,
    ) -> None:
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "timeout": timeout,
            "retries": retries,
            "client": client,
            "on_text_update": on_text_update,
            "on_status_update": on_status_update,
            "knowledge_base_tool": knowledge_base_tool,
            "ai_tool_rag": ai_tool_rag,
            "max_knowledge_base_tool_calls": max_knowledge_base_tool_calls,
        }
        if sleep is not None:
            kwargs["sleep"] = sleep
        try:
            self.transport = ResponsesReportService(**kwargs)
        except ReportConfigurationError as exc:
            raise ConstructionPlanConfigurationError(str(exc)) from exc
        self.model = self.transport.model
        self._on_text_update = on_text_update

    @staticmethod
    def plan_json_schema(source_markers: Iterable[str] = (), *, allow_unknown: bool = False) -> dict[str, Any]:
        schema = ConstructionPlanDraft.model_json_schema()
        work_items = schema.get("properties", {}).get("work_items")
        if isinstance(work_items, dict):
            work_items["minItems"] = 1
            work_items["maxItems"] = 1
        schema["$schema"] = "http://json-schema.org/draft-07/schema#"
        return _constrain_reference_values(_make_schema_strict(schema), source_markers, allow_unknown=allow_unknown)

    def _request_draft(
        self,
        *,
        report_payload: dict[str, Any],
        repair_lines: list[dict[str, Any]],
        generation_context: GenerationContext,
        on_text_update: Callable[[str], None] | None = None,
    ) -> tuple[ConstructionPlanDraft, str]:
        allowed_source_markers = sorted(_allowed_source_markers(generation_context))
        if getattr(self.transport, "_ai_tool_rag", False):
            # Tool results arrive during the model turn; source markers are
            # validated after the callback has appended them to the context.
            allowed_source_markers = []
        requested_identities = [_identity(line) for line in repair_lines]
        requested_identity_labels = [
            f"{image_name}#{finding_index}"
            for image_name, finding_index in requested_identities
        ]
        # `GenerationContext.prompt` contains the complete batch evidence. A
        # chunked construction request must receive only its scoped report and
        # repair lines, otherwise the model can bind the response to another
        # finding and the request becomes unnecessarily large.
        payload_data = {
            "project_overview": _project_overview(report_payload, generation_context),
            "validated_damage_report": report_payload,
            "deterministic_repair_plan": {
                "lines": [_ai_repair_line(line) for line in repair_lines]
            },
            "requested_work_item_identities": requested_identity_labels,
            "knowledge_base": generation_context.retrieved_chunks,
            "allowed_source_markers": allowed_source_markers,
            "profile": generation_context.profile_name,
            "settings_snapshot_id": generation_context.settings_snapshot_id,
        }
        scoped_identity_instruction = (
            "This request is scoped to exactly these work-item identities: "
            + ", ".join(requested_identity_labels)
            + ". Return exactly one work item for each listed identity, in the listed order. "
            "Do not select an identity from any other finding or from prior requests."
        )
        business_prompt = (
            generation_context.profile_prompt.strip()
            or "依确认事实扩写施工方案，所有现场不确定项必须保留人工复核条件。"
        )
        output_text = self.transport.request_structured_output(
            payload_data=payload_data,
            instructions="\n\n".join(
                [CONSTRUCTION_PLAN_INSTRUCTIONS, scoped_identity_instruction, business_prompt]
            ),
            schema_name="construction_plan_draft",
            schema=self.plan_json_schema(
                allowed_source_markers,
                allow_unknown=getattr(self.transport, "_ai_tool_rag", False),
            ),
            operation_name="construction plan draft",
            max_output_tokens=max(16000, min(32000, 12000 + len(repair_lines) * 4000)),
            on_text_update=on_text_update,
        )
        draft = _clean_legacy_rule_card_labels(
            ConstructionPlanDraft.model_validate(
                simplify_chinese_value(json.loads(output_text))
            )
        )
        expected = [_identity(line) for line in repair_lines]
        _validate_exact_identities(
            expected, [_identity(item) for item in draft.work_items], label="construction draft"
        )
        _validate_references(draft, generation_context)
        return draft, output_text

    def generate_plan(
        self,
        *,
        report: Any,
        repair_plan: dict[str, Any],
        generation_context: GenerationContext,
    ) -> ConstructionPlan:
        try:
            report_payload = _confirmed_report_payload(report)
            lines = _validate_repair_plan(report_payload, repair_plan)
            if len(lines) > 1:
                draft = self._generate_chunked_draft(
                    report_payload=report_payload,
                    repair_lines=lines,
                    generation_context=generation_context,
                )
            else:
                draft, _ = self._request_draft(
                    report_payload=report_payload,
                    repair_lines=lines,
                    generation_context=generation_context,
                )
            return _assemble_plan(
                report_payload=report_payload,
                repair_plan=repair_plan,
                draft=draft,
                model=self.model,
                generation_context=generation_context,
                method_source="remote_ai",
            )
        except (ReportGenerationError, ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ConstructionPlanGenerationError(str(exc)) from exc

    def _generate_chunked_draft(
        self,
        *,
        report_payload: dict[str, Any],
        repair_lines: list[dict[str, Any]],
        generation_context: GenerationContext,
    ) -> ConstructionPlanDraft:
        batches = [[line] for line in repair_lines]
        drafts: list[ConstructionPlanDraft] = []
        completed_text = ""
        for batch in batches:
            identities = [_identity(line) for line in batch]

            def cumulative_update(text: str, prefix: str = completed_text) -> None:
                if self._on_text_update is not None:
                    self._on_text_update(prefix + text)

            draft, raw = self._request_draft(
                report_payload=_report_subset(report_payload, identities),
                repair_lines=batch,
                generation_context=generation_context,
                on_text_update=cumulative_update if self._on_text_update is not None else None,
            )
            drafts.append(draft)
            completed_text += raw + "\n"

        first = drafts[0]
        merged = ConstructionPlanDraft(
            scope=first.scope,
            executive_summary=first.executive_summary,
            preconstruction_checks=list(first.preconstruction_checks),
            work_items=[item for draft in drafts for item in draft.work_items],
            general_quality_requirements=list(first.general_quality_requirements),
            general_safety_requirements=list(first.general_safety_requirements),
            post_repair_inspection=list(first.post_repair_inspection),
            schedule_assumptions=list(first.schedule_assumptions),
            excluded_items=list(first.excluded_items),
            limitations=list(first.limitations),
            knowledge_references=list(first.knowledge_references),
        )
        for draft in drafts[1:]:
            _append_unique(merged.preconstruction_checks, draft.preconstruction_checks)
            _append_unique(merged.general_quality_requirements, draft.general_quality_requirements)
            _append_unique(merged.general_safety_requirements, draft.general_safety_requirements)
            _append_unique(merged.post_repair_inspection, draft.post_repair_inspection)
            _append_unique(merged.schedule_assumptions, draft.schedule_assumptions)
            _append_unique(merged.excluded_items, draft.excluded_items)
            _append_unique(merged.limitations, draft.limitations)
            _append_unique(merged.knowledge_references, draft.knowledge_references)
        _validate_exact_identities(
            [_identity(line) for line in repair_lines],
            [_identity(item) for item in merged.work_items],
            label="merged construction draft",
        )
        return merged

    def generate_plan_and_persist(
        self,
        *,
        report: Any,
        repair_plan: dict[str, Any],
        output_dir: str | Path,
        generation_context: GenerationContext,
    ) -> ConstructionPlan:
        plan = self.generate_plan(
            report=report,
            repair_plan=repair_plan,
            generation_context=generation_context,
        )
        persist_construction_plan(
            plan,
            repair_plan=repair_plan,
            output_dir=output_dir,
            generation_context=generation_context,
            fallback=False,
        )
        return plan


def _local_details(damage_type: str) -> dict[str, list[str]]:
    normalized = damage_type.casefold()
    common = {
        "applicability": ["僅在損傷身份、範圍、基材狀態與人工確認報告一致時適用"],
        "measurements": ["複測實體位置、範圍、深度及相關構件狀態"],
        "materials": ["與原基材相容且經工程師批准的修復材料"],
        "equipment": ["現場量測、基面處理、修復及安全防護設備"],
        "procedures": ["核對損傷編號並隔離作業區", "完成現場複測與工程師工法確認", "按批准工法處理基面並施工", "依材料要求養護並留存記錄", "完成修復後複檢"],
        "quality": ["損傷範圍、基層狀態、材料批次、關鍵工序及養護記錄可追溯"],
        "acceptance": ["修復範圍與批准方案一致", "工程師要求的外觀及專項複檢合格"],
        "safety": ["完成作業隔離、風險交底與個人防護"],
    }
    if "crack" in normalized:
        common.update(
            measurements=["複測裂縫位置、走向、實際長度、寬度、深度及活動性"],
            materials=["與基材及裂縫狀態相容、經批准的封閉或低壓灌注材料"],
            equipment=["裂縫測寬與位置記錄設備", "經工法確認的清理、封閉或低壓灌注設備"],
            procedures=["編號並標記裂縫走向與端點", "複測活動性並確認封閉或灌注工法", "清理裂縫及基層", "按批准工藝修復並控制施工連續性", "養護後完成外觀與裂縫複測"],
            quality=["裂縫複測記錄與影像編號一致", "基面、材料適用期及施工連續性可追溯"],
            acceptance=["修復範圍覆蓋批准的裂縫邊界", "複檢未見異常發展、明顯滲漏或脫黏"],
            safety=["按材料及壓力設備要求作業", "疑似受力異常時立即停工並專項複核"],
        )
    elif "corrosion" in normalized or "rebar" in normalized:
        common.update(
            measurements=["複測鋼筋暴露、鏽蝕與保護層異常範圍及實體深度"],
            materials=["經批准的鋼筋防護材料", "與原混凝土相容的截面修復材料"],
            procedures=["標記異常邊界", "確認剔除範圍及鋼筋處理要求", "受控清除鬆動層並處理鋼筋與基層", "實施防護及截面修復", "養護後複檢"],
        )
    elif "deformation" in normalized:
        common.update(
            applicability=["專項檢測與結構評估完成前不適用直接修補"],
            measurements=["建立基準並複測構件幾何、支承、荷載及變形趨勢"],
            materials=["專項評估前不預設修復材料"],
            equipment=["滿足精度要求的幾何量測與監測設備"],
            procedures=["隔離風險區並暫停直接修補", "建立監測基準", "核查支承與荷載", "形成並批准專項處置方案", "按批准方案實施及持續監測"],
        )
    return common


def _local_draft(
    lines: list[dict[str, Any]],
    reason: str,
    diagnostic: ConstructionFallbackDiagnostic,
) -> ConstructionPlanDraft:
    # A truncated remote stream must remain auditable as a local fallback, but
    # the review form still needs a concrete, finding-specific method.  The
    # repair-plan lines are deterministic evidence-derived inputs and are safe
    # to use as a conservative preview; they are never relabelled as remote AI.
    unavailable = "远端施工方案草稿不完整；以下为确定性保守预览，须经工程师现场复核。"
    unavailable_list = [unavailable]
    method_names = {
        "Structural crack": "裂缝封闭或灌注",
        "Microcrack": "裂缝封闭或灌注",
        "Concrete crushing": "混凝土局部修补",
        "Delamination": "混凝土局部修补",
        "Minor spalling": "剥落区截面修复",
        "Moderate spalling": "剥落区截面修复",
        "Rebar corrosion": "钢筋锈蚀与保护层修复",
        "Structural deformation": "暂停施工并专项评估",
    }
    items = []
    for line in lines:
        damage_type = str(line.get("class_name", "未知損傷"))
        details = _local_details(damage_type)
        method_name = str(line.get("method_display_name") or "").strip() or method_names.get(
            damage_type, "现场复核后确定修复工法"
        )
        repair_method = str(line.get("repair_method") or "").strip()
        if not repair_method:
            repair_method = {
                "Structural crack": "复测裂缝活动性后，由工程师确认采用表面封闭或低压灌注工法",
                "Microcrack": "复测裂缝活动性后，由工程师确认采用表面封闭或低压灌注工法",
                "Rebar corrosion": "复核钢筋状态后，按批准范围除锈、防护并恢复混凝土保护层",
                "Structural deformation": "暂不设定直接修复工法；隔离风险区并完成专项检测与评估",
            }.get(damage_type, "受控清除松动或劣化部位，完成界面处理、相容材料修补及养护")
        items.append(
            ConstructionWorkItemDraft(
                image_name=str(line.get("image_name", "")),
                finding_index=int(line.get("finding_index", 0)),
                proposed_method_name=method_name,
                proposed_repair_method=repair_method,
                method_rationale=(str(reason).strip() or unavailable)[:600],
                applicability_conditions=unavailable_list,
                required_site_measurements=unavailable_list,
                materials=unavailable_list,
                equipment=unavailable_list,
                procedure_steps=unavailable_list,
                quality_control_points=unavailable_list,
                acceptance_checks=unavailable_list,
                safety_controls=unavailable_list,
                method_required_site_verification=unavailable_list,
                method_upgrade_conditions=unavailable_list,
                method_code_references=unavailable_list,
                stop_work_conditions=unavailable_list,
                method_excluded_conclusions=unavailable_list,
                assumptions=unavailable_list,
                knowledge_references=[],
            )
        )
    return ConstructionPlanDraft(
        scope=unavailable,
        executive_summary=diagnostic.summary_template.format(count=len(items)),
        preconstruction_checks=unavailable_list,
        work_items=items,
        general_quality_requirements=unavailable_list,
        general_safety_requirements=unavailable_list,
        post_repair_inspection=unavailable_list,
        schedule_assumptions=unavailable_list,
        excluded_items=unavailable_list,
        limitations=[f"{diagnostic.limitation_prefix}：{reason}", unavailable],
        knowledge_references=[],
    )


def build_local_fallback_construction_plan(
    *,
    report: Any,
    repair_plan: dict[str, Any],
    output_dir: str | Path,
    reason: str,
    generation_context: GenerationContext,
) -> ConstructionPlan:
    redacted_reason = redact_secret_text(str(reason))[:1000]
    diagnostic = classify_construction_fallback_reason(redacted_reason)
    report_payload = _confirmed_report_payload(report)
    lines = _validate_repair_plan(report_payload, repair_plan)
    plan = _assemble_plan(
        report_payload=report_payload,
        repair_plan=repair_plan,
        draft=_local_draft(lines, redacted_reason, diagnostic),
        model="local-repair-plan-fallback",
        generation_context=generation_context,
        method_source="local_fallback",
    )
    persist_construction_plan(
        plan,
        repair_plan=repair_plan,
        output_dir=output_dir,
        generation_context=generation_context,
        fallback=True,
        fallback_reason=redacted_reason,
        fallback_category=diagnostic.category,
    )
    return plan


def persist_construction_plan(
    plan: ConstructionPlan,
    *,
    repair_plan: dict[str, Any],
    output_dir: str | Path,
    generation_context: GenerationContext,
    fallback: bool,
    fallback_reason: str = "",
    fallback_category: str = "",
) -> tuple[Path, Path]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    plan = simplify_chinese_model(plan, ConstructionPlan)
    json_path = target_dir / "construction_plan.json"
    markdown_path = target_dir / "construction_plan.md"
    safe_reason = redact_secret_text(str(fallback_reason))[:1000] if fallback else ""
    generation_audit = {
        "generation_mode": "local_fallback" if fallback else "remote_ai_draft_local_assembly",
        "model": plan.provenance.model,
        "knowledge_base_used": bool(generation_context.knowledge_base_used),
        "knowledge_base_status": generation_context.knowledge_base_status,
        "fallback_reason": safe_reason,
        "fallback_category": fallback_category if fallback else "",
        "immutable_fields_assembled_locally": True,
        "repair_method_source": (
            plan.work_items[0].repair_method_source if plan.work_items else "local_fallback"
        ),
        "construction_released": False,
    }
    citation_catalog = build_citation_catalog(generation_context.retrieved_chunks)
    atomic_write_json(
        json_path,
        {
            "construction_plan": plan.model_dump(mode="json"),
            "repair_plan_snapshot": simplify_chinese_value(repair_plan),
            "plan_schema_version": CONSTRUCTION_PLAN_SCHEMA_VERSION,
            "fallback": bool(fallback),
            "fallback_reason": safe_reason,
            "fallback_category": fallback_category if fallback else "",
            "generation_audit": generation_audit,
            "citation_catalog": citation_catalog,
        },
    )
    atomic_write_text(
        markdown_path,
        render_construction_plan_markdown(plan, citation_catalog=citation_catalog),
    )
    profile_manifest = generation_context.manifest(model=plan.provenance.model)
    profile_manifest["construction_plan_audit"] = generation_audit
    merge_profile_manifest(
        target_dir / "generation_manifest.json",
        profile_name=generation_context.profile_name,
        profile_manifest=profile_manifest,
        outputs={"json": str(json_path), "markdown": str(markdown_path)},
        fallback=fallback,
        fallback_reason=safe_reason,
    )
    return json_path, markdown_path


def load_construction_plan(path: str | Path) -> ConstructionPlan:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    nested = payload.get("construction_plan") if isinstance(payload, dict) else None
    return simplify_chinese_model(
        ConstructionPlan.model_validate(nested if isinstance(nested, dict) else payload),
        ConstructionPlan,
    )


_EDITABLE_PLAN_FIELDS = (
    "scope",
    "executive_summary",
    "preconstruction_checks",
    "general_quality_requirements",
    "general_safety_requirements",
    "post_repair_inspection",
    "schedule_assumptions",
    "excluded_items",
    "limitations",
)

_EDITABLE_WORK_ITEM_FIELDS = (
    "applicability_conditions",
    "required_site_measurements",
    "materials",
    "equipment",
    "procedure_steps",
    "quality_control_points",
    "acceptance_checks",
    "safety_controls",
    "assumptions",
)

_EDITABLE_WORK_ITEM_TEXT_FIELDS = (
    "ai_method_name",
    "base_repair_method",
    "method_rationale",
)


def _merge_reviewed_construction_plan(
    original: ConstructionPlan,
    proposed: ConstructionPlan,
    *,
    confirmed: bool,
) -> ConstructionPlan:
    if len(proposed.work_items) != len(original.work_items):
        raise ValueError("施工分项数量与原方案不一致")
    original_identities = [
        (item.work_item_id, item.repair_item_id, item.finding_id)
        for item in original.work_items
    ]
    proposed_identities = [
        (item.work_item_id, item.repair_item_id, item.finding_id)
        for item in proposed.work_items
    ]
    if proposed_identities != original_identities:
        raise ValueError("施工分项身份或顺序与原方案不一致")

    reviewed = original.model_copy(deep=True)
    for field_name in _EDITABLE_PLAN_FIELDS:
        value = getattr(proposed, field_name)
        setattr(reviewed, field_name, list(value) if isinstance(value, list) else value)
    for source_item, target_item in zip(proposed.work_items, reviewed.work_items):
        for field_name in _EDITABLE_WORK_ITEM_TEXT_FIELDS:
            setattr(target_item, field_name, str(getattr(source_item, field_name)).strip())
        for field_name in _EDITABLE_WORK_ITEM_FIELDS:
            setattr(target_item, field_name, list(getattr(source_item, field_name)))

    reviewer = proposed.human_review.reviewer.strip()
    notes = proposed.human_review.notes.strip()
    if confirmed and not reviewer:
        raise ValueError("确认施工方案时必须填写审核人")
    review_status = "confirmed_by_engineer" if confirmed else "edited_pending_confirmation"
    reviewed.review_status = review_status
    reviewed.human_review = ConstructionPlanReview(
        status=review_status,
        reviewer=reviewer,
        reviewed_at=datetime.now(timezone.utc).isoformat() if confirmed else None,
        notes=notes,
    )
    reviewed.plan_status = original.plan_status
    reviewed.construction_released = False
    reviewed.plan_schema_version = original.plan_schema_version
    reviewed.provenance = original.provenance.model_copy(deep=True)
    return simplify_chinese_model(
        ConstructionPlan.model_validate(reviewed.model_dump(mode="json")), ConstructionPlan
    )


def save_human_reviewed_construction_plan(
    path: str | Path,
    plan: ConstructionPlan | dict[str, Any],
    *,
    confirmed: bool,
) -> ConstructionPlan:
    target = Path(path)
    envelope = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or not isinstance(envelope.get("construction_plan"), dict):
        raise ValueError("施工方案文件缺少 construction_plan 数据")
    original = ConstructionPlan.model_validate(envelope["construction_plan"])
    proposed = plan if isinstance(plan, ConstructionPlan) else ConstructionPlan.model_validate(plan)
    reviewed = _merge_reviewed_construction_plan(original, proposed, confirmed=confirmed)
    envelope["construction_plan"] = reviewed.model_dump(mode="json")
    citation_catalog = normalize_citation_catalog(envelope.get("citation_catalog"))
    envelope["citation_catalog"] = citation_catalog
    atomic_write_json(target, envelope)
    atomic_write_text(
        target.with_name("construction_plan.md"),
        render_construction_plan_markdown(reviewed, citation_catalog=citation_catalog),
    )
    return reviewed


def _markdown_list(values: Iterable[str], *, empty: str = "无") -> list[str]:
    rendered = [f"- {value}" for value in values if str(value).strip()]
    return rendered or [f"- {empty}"]


def render_construction_plan_markdown(
    plan: ConstructionPlan,
    *,
    citation_catalog: Iterable[dict[str, Any]] | None = None,
) -> str:
    plan = simplify_chinese_model(plan, ConstructionPlan)
    status_labels = {
        "pending_engineer_review": "待工程师审核",
        "hold": "暂停施工，待专项评估",
        "evidence_inconsistent": "证据不一致，禁止施工",
    }
    cite = lambda value: render_knowledge_citations(
        value,
        citation_catalog,
        traditional=False,
    )
    cited_list = lambda values: [cite(value) for value in values]
    lines = [
        "# 混凝土构件修复施工方案",
        "",
        f"**方案版本**：`{plan.plan_schema_version}`  ",
        f"**方案状态**：{status_labels.get(plan.plan_status, plan.plan_status)}  ",
        f"**审核状态**：{plan.review_status}  ",
        "**施工放行**：否  ",
        f"**证据一致性**：{plan.evidence_consistency_status}（{plan.actual_evidence_count}/{plan.expected_evidence_count}）",
        "",
        "> 本文件为施工草案。未经现场复核、工程师审核及正式批准，不得据此直接施工。",
        "",
    ]
    if plan.project_overview:
        lines.extend(["## 项目概况", "", cite(plan.project_overview), ""])
    lines.extend([
        "## 方案摘要", "", cite(plan.executive_summary), "",
        "## 证据与适用范围", "", cite(plan.evidence_consistency_note), "", cite(plan.scope), "",
        "## 施工前复核", "", *_markdown_list(cited_list(plan.preconstruction_checks)), "",
        "## 损伤与分项工法", "",
    ])
    for item in plan.work_items:
        lines.extend([
            f"### {item.work_item_id} · {item.image_name} · {item.damage_type}", "",
            f"- 损伤身份：`{item.finding_id}`",
            f"- 人工确认等级：{item.damage_level}",
            f"- 等级依据：{cite(item.report_damage_level_reason or '未填写')}",
            f"- 可见证据：{cite(item.report_observed_evidence or '未填写')}",
            f"- 风险解读：{cite(item.report_risk_interpretation or '未填写')}",
            f"- 报告建议：{cite(item.report_recommended_action or '未填写')}",
            f"- 不确定性：{cite(item.report_uncertainty or '未填写')}",
            f"- 决策状态：{item.decision_status}",
            f"- 工法来源：{item.repair_method_source}",
            f"- 工法名称：{cite(item.ai_method_name or item.method_display_name)}",
            f"- 修复工法：{cite(item.base_repair_method)}",
            f"- 工法理由：{cite(item.method_rationale or '未填写')}",
            f"- 工程量依据：{cite(item.quantity_basis)}",
            "- 构件面积占比：无可独立验证尺度，保持空值",
            "- 实体面积：无可独立验证尺度，保持空值", "",
            "#### 图像与占位", "",
        ])
        for figure in item.figures:
            value = figure.path if figure.path else "待補充"
            lines.append(f"- {figure.caption}：{value}（{figure.availability}）")
        sections = (
            ("报告标准依据", [cite(f"{ref.get('id', '')} {ref.get('name', '')}：{ref.get('role', '')}") for ref in item.report_standards_basis]),
            ("报告视觉依据", item.report_visual_basis),
            ("适用条件", item.applicability_conditions),
            ("AI 工法现场核验", item.method_required_site_verification),
            ("现场复测", item.required_site_measurements),
            ("升级评估条件", item.method_upgrade_conditions),
            ("材料", item.materials),
            ("設備", item.equipment),
        )
        for title, values in sections:
            lines.extend(["", f"#### {title}", "", *_markdown_list(cited_list(values))])
        lines.extend(["", "#### 施工步骤", ""])
        lines.extend([f"{index}. {cite(value)}" for index, value in enumerate(item.procedure_steps, start=1)] or ["1. 待工程师补充"])
        for title, values in (
            ("质量控制", item.quality_control_points),
            ("验收检查", item.acceptance_checks),
            ("安全控制", item.safety_controls),
            ("停工条件", item.stop_work_conditions),
            ("不得推定事项", item.method_excluded_conclusions),
            ("假设与限制", item.assumptions),
            ("知识库引用", item.knowledge_references),
        ):
            lines.extend(["", f"#### {title}", "", *_markdown_list(cited_list(values))])
        lines.append("")
    for title, values in (
        ("总体质量要求", plan.general_quality_requirements),
        ("总体安全要求", plan.general_safety_requirements),
        ("修复后复检", plan.post_repair_inspection),
        ("工期假设", plan.schedule_assumptions),
        ("不包含事项", plan.excluded_items),
        ("方案局限", plan.limitations),
        ("知识库参考", plan.knowledge_references),
    ):
        lines.extend([f"## {title}", "", *_markdown_list(cited_list(values)), ""])
    review_lines = [
        f"- 审核状态：{plan.review_status}",
        f"- 审核人：{plan.human_review.reviewer or '未填写'}",
        f"- 审核时间：{plan.human_review.reviewed_at or '未确认'}",
    ]
    if plan.human_review.notes:
        review_lines.append(f"- 审核备注：{plan.human_review.notes}")
    lines.extend([
        "## 审核与放行", "", *review_lines,
        "- 本方案一律保持 `construction_released=false`。",
        "- 工程师审核、必要的专项设计与正式施工批准须在系统外另行完成。", "",
    ])
    return "\n".join(lines)
