from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


CONSTRUCTION_PLAN_SCHEMA_VERSION = "construction-plan.v2"
DraftListText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ConstructionFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figure_type: str
    path: str | None = None
    caption: str
    availability: str


class ConstructionWorkItemDraft(BaseModel):
    """AI-authored method draft; confirmed facts and controls stay local."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    image_name: str = Field(min_length=1, max_length=260)
    finding_index: int
    proposed_method_name: str = Field(min_length=1, max_length=120)
    proposed_repair_method: str = Field(min_length=1, max_length=1200)
    method_rationale: str = Field(min_length=1, max_length=600)
    applicability_conditions: list[DraftListText] = Field(min_length=1, max_length=4)
    required_site_measurements: list[DraftListText] = Field(min_length=1, max_length=5)
    materials: list[DraftListText] = Field(min_length=1, max_length=5)
    equipment: list[DraftListText] = Field(min_length=1, max_length=5)
    procedure_steps: list[DraftListText] = Field(min_length=1, max_length=8)
    quality_control_points: list[DraftListText] = Field(min_length=1, max_length=5)
    acceptance_checks: list[DraftListText] = Field(min_length=1, max_length=5)
    safety_controls: list[DraftListText] = Field(min_length=1, max_length=5)
    method_required_site_verification: list[DraftListText] = Field(min_length=1, max_length=5)
    method_upgrade_conditions: list[DraftListText] = Field(min_length=1, max_length=5)
    method_code_references: list[DraftListText] = Field(min_length=1, max_length=5)
    stop_work_conditions: list[DraftListText] = Field(min_length=1, max_length=5)
    method_excluded_conclusions: list[DraftListText] = Field(min_length=1, max_length=5)
    assumptions: list[DraftListText] = Field(min_length=1, max_length=5)
    knowledge_references: list[DraftListText] = Field(default_factory=list, max_length=8)


class ConstructionPlanDraft(BaseModel):
    """Strict remote-output contract without status, release or provenance fields."""

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=800)
    executive_summary: str = Field(min_length=1, max_length=1000)
    preconstruction_checks: list[DraftListText] = Field(min_length=1, max_length=4)
    work_items: list[ConstructionWorkItemDraft]
    general_quality_requirements: list[DraftListText] = Field(min_length=1, max_length=4)
    general_safety_requirements: list[DraftListText] = Field(min_length=1, max_length=4)
    post_repair_inspection: list[DraftListText] = Field(min_length=1, max_length=4)
    schedule_assumptions: list[DraftListText] = Field(min_length=1, max_length=4)
    excluded_items: list[DraftListText] = Field(min_length=1, max_length=4)
    limitations: list[DraftListText] = Field(min_length=1, max_length=4)
    knowledge_references: list[DraftListText] = Field(default_factory=list, max_length=8)


class ConstructionWorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_item_id: str
    repair_item_id: str
    finding_id: str
    image_name: str
    finding_index: int
    original_image_path: str | None = None
    annotated_image_path: str | None = None
    construction_image_path: str | None = None
    figures: list[ConstructionFigure] = Field(default_factory=list)
    damage_type: str
    damage_level: str
    report_damage_level_reason: str
    report_standards_basis: list[dict[str, str]]
    report_visual_basis: list[str]
    report_observed_evidence: str
    report_risk_interpretation: str
    report_recommended_action: str
    report_uncertainty: str
    method_id: str
    method_name: str
    method_display_name: str
    base_repair_method: str
    ai_method_name: str = ""
    method_rationale: str = ""
    repair_method_source: Literal["remote_ai", "local_fallback", "legacy_local"] = "legacy_local"
    decision_status: str
    quantity_basis: str
    component_area_ratio: float | None = None
    physical_area_mm2: float | None = None
    applicability_conditions: list[str]
    required_site_measurements: list[str]
    method_required_site_verification: list[str]
    method_upgrade_conditions: list[str]
    method_code_references: list[str]
    materials: list[str]
    equipment: list[str]
    procedure_steps: list[str]
    quality_control_points: list[str]
    acceptance_checks: list[str]
    safety_controls: list[str]
    stop_work_conditions: list[str]
    method_excluded_conclusions: list[str]
    assumptions: list[str]
    review_required: bool
    knowledge_references: list[str] = Field(default_factory=list)


class ConstructionPlanProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    generated_at: str
    source_report_hash: str
    repair_plan_hash: str
    settings_snapshot_id: str
    human_review_required: bool


class ConstructionPlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "pending_engineer_review"
    reviewer: str = ""
    reviewed_at: str | None = None
    notes: str = ""


class ConstructionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_schema_version: str
    plan_status: str
    construction_released: bool
    project_id: str | None = None
    report_id: str | None = None
    project_overview: str | None = None
    expected_evidence_count: int
    actual_evidence_count: int
    evidence_consistency_status: str
    evidence_consistency_note: str
    scope: str
    executive_summary: str
    preconstruction_checks: list[str]
    work_items: list[ConstructionWorkItem]
    general_quality_requirements: list[str]
    general_safety_requirements: list[str]
    post_repair_inspection: list[str]
    schedule_assumptions: list[str]
    excluded_items: list[str]
    limitations: list[str]
    knowledge_references: list[str] = Field(default_factory=list)
    provenance: ConstructionPlanProvenance
    review_status: str = "pending_engineer_review"
    human_review: ConstructionPlanReview = Field(default_factory=ConstructionPlanReview)


def new_construction_plan_provenance(
    *,
    model: str,
    source_report_hash: str,
    repair_plan_hash: str,
    settings_snapshot_id: str,
) -> ConstructionPlanProvenance:
    return ConstructionPlanProvenance(
        model=model,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_report_hash=source_report_hash,
        repair_plan_hash=repair_plan_hash,
        settings_snapshot_id=settings_snapshot_id,
        human_review_required=True,
    )
