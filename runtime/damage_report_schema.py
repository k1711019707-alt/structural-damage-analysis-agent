from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


REPORT_SCHEMA_VERSION = "damage-report.v3"
DamageLevel = Literal["undetermined", "low", "medium", "high", "critical"]
ReportListText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=240),
]

_LEVEL_ALIASES = {
    "": "undetermined",
    "unknown": "undetermined",
    "undetermined": "undetermined",
    "待判定": "undetermined",
    "未判定": "undetermined",
    "未证实": "undetermined",
    "未證實": "undetermined",
    "低": "low",
    "低风险": "low",
    "低風險": "low",
    "low": "low",
    "中": "medium",
    "中等": "medium",
    "中风险": "medium",
    "中風險": "medium",
    "medium": "medium",
    "高": "high",
    "高风险": "high",
    "高風險": "high",
    "high": "high",
    "严重": "critical",
    "嚴重": "critical",
    "危急": "critical",
    "critical": "critical",
}


def normalize_damage_level(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[normalized]
    raise ValueError(f"unsupported damage level: {value!r}")


def finding_identity(item: Any) -> tuple[str, int]:
    if isinstance(item, dict):
        return str(item.get("image_name", "")), int(item.get("finding_index", item.get("index", 0)))
    return str(getattr(item, "image_name", "")), int(
        getattr(item, "finding_index", getattr(item, "index", 0))
    )


class StandardReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=240)


class DamageFinding(BaseModel):
    """Minimal recognition hint. Geometric fields from legacy summaries are ignored."""

    model_config = ConfigDict(extra="ignore")

    index: int
    image_name: str
    class_id: int = -1
    class_name: str
    score: float = 0.0
    detection_confidence: float = 0.0


class VisualEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image_name: str
    image_path: str
    overlay_path: str | None = None
    processing_status: str = "unknown"


class ReportSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str | None = Field(max_length=240)
    asset_id: str | None = Field(max_length=240)
    component: str | None = Field(max_length=240)
    inspection_time: str | None = Field(max_length=120)
    project_overview: str | None = Field(default=None, max_length=4000)


class ReportFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    finding_index: int
    image_name: str = Field(min_length=1, max_length=260)
    damage_type: str = Field(min_length=1, max_length=240)
    damage_level: DamageLevel = "undetermined"
    level_reason: str = Field(default="", max_length=1200)
    standards_basis: list[StandardReference] = Field(default_factory=list, max_length=8)
    visual_basis: list[ReportListText] = Field(default_factory=list, max_length=8)
    uncertainty: str = Field(default="", max_length=1000)
    observed_evidence: str = Field(min_length=1, max_length=1600)
    risk_interpretation: str = Field(min_length=1, max_length=1200)
    recommended_action: str = Field(min_length=1, max_length=1600)
    confidence_note: str = Field(min_length=1, max_length=800)

    @field_validator("damage_level", mode="before")
    @classmethod
    def _normalize_level(cls, value: Any) -> str:
        return normalize_damage_level(value)


class DamageReportDraft(BaseModel):
    """Business content returned by the model; audit and review fields are local-only."""

    model_config = ConfigDict(extra="forbid")

    subject: ReportSubject
    executive_summary: str = Field(min_length=1, max_length=1600)
    overall_screening_level: DamageLevel
    overall_level_reason: str = Field(default="", max_length=1200)
    findings: list[ReportFinding]
    limitations: list[ReportListText] = Field(max_length=8)

    @field_validator("overall_screening_level", mode="before")
    @classmethod
    def _normalize_level(cls, value: Any) -> str:
        return normalize_damage_level(value)


class HumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "pending_human_review"
    reviewer: str = ""
    reviewed_at: str | None = None
    notes: str = ""


class ReportIntegrity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correspondence_valid: bool = True
    expected_count: int = 0
    actual_count: int = 0
    issues: list[str] = Field(default_factory=list)


class ReportProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    generated_at: str
    source_summary_path: str
    evidence_count: int
    report_schema_version: str
    human_review_required: bool


class DamageReport(DamageReportDraft):
    model_config = ConfigDict(extra="forbid")

    report_schema_version: str
    review_status: str
    human_review: HumanReview = Field(default_factory=HumanReview)
    integrity: ReportIntegrity = Field(default_factory=ReportIntegrity)
    provenance: ReportProvenance


class DamageReportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    subject: ReportSubject
    findings: list[DamageFinding]
    visual_evidence: list[VisualEvidence] = Field(default_factory=list)
    source_summary_path: str
    model_provenance: dict[str, Any]
    inference_provenance: dict[str, Any]

    @classmethod
    def from_summary(cls, summary: dict[str, Any], source_summary_path: str) -> "DamageReportRequest":
        raw_findings: list[dict[str, Any]] = []
        visual_evidence: list[VisualEvidence] = []
        for item in summary.get("results", []):
            image_name = str(item.get("image_name", "") or "")
            image_path = str(item.get("image_path", "") or "")
            if image_name and image_path:
                visual_evidence.append(
                    VisualEvidence(
                        image_name=image_name,
                        image_path=image_path,
                        overlay_path=str(item.get("overlay_path", "") or "") or None,
                        processing_status=str(item.get("status", "unknown") or "unknown"),
                    )
                )
            for finding in item.get("damage_findings") or []:
                raw_findings.append({**finding, "image_name": image_name})
        return cls(
            subject=ReportSubject(
                project_name=None,
                asset_id=None,
                component=None,
                inspection_time=None,
                project_overview=str(summary.get("project_overview", "") or "") or None,
            ),
            findings=[DamageFinding.model_validate(item) for item in raw_findings],
            visual_evidence=visual_evidence,
            source_summary_path=source_summary_path,
            model_provenance=dict(summary.get("model_provenance") or {}),
            inference_provenance=dict(summary.get("inference_provenance") or {}),
        )

    def identities(self) -> list[tuple[str, int]]:
        return [finding_identity(item) for item in self.findings]

    def prompt_payload(self) -> dict[str, Any]:
        finding_images = {finding.image_name for finding in self.findings}
        return {
            "subject": self.subject.model_dump(mode="json"),
            "project_overview": self.subject.project_overview,
            "detection_hints": [
                {
                    "finding_index": item.index,
                    "image_name": item.image_name,
                    "detected_damage_type": item.class_name,
                    "detection_confidence": item.detection_confidence,
                }
                for item in self.findings
            ],
            "visual_evidence": [
                {
                    "image_name": item.image_name,
                    "processing_status": item.processing_status,
                    "attachments": [
                        "original_image",
                        *(["recognition_overlay"] if item.overlay_path else []),
                    ],
                }
                for item in self.visual_evidence
                if item.image_name in finding_images
            ],
            "model_provenance": self.model_provenance,
            "inference_provenance": self.inference_provenance,
        }


def validate_and_order_findings(
    expected: list[tuple[str, int]], findings: list[ReportFinding]
) -> tuple[list[ReportFinding], ReportIntegrity]:
    actual = [finding_identity(item) for item in findings]
    expected_counts = Counter(expected)
    actual_counts = Counter(actual)
    issues: list[str] = []
    for identity, count in expected_counts.items():
        actual_count = actual_counts.get(identity, 0)
        if actual_count < count:
            issues.append(f"missing:{identity[0]}#{identity[1]}")
        elif actual_count > count:
            issues.append(f"duplicate:{identity[0]}#{identity[1]}")
    for identity in actual_counts.keys() - expected_counts.keys():
        issues.append(f"added:{identity[0]}#{identity[1]}")
    integrity = ReportIntegrity(
        correspondence_valid=not issues and len(expected) == len(actual),
        expected_count=len(expected),
        actual_count=len(actual),
        issues=issues,
    )
    if not integrity.correspondence_valid:
        raise ValueError("report finding correspondence mismatch: " + ", ".join(issues))
    by_identity = {finding_identity(item): item for item in findings}
    return [by_identity[identity] for identity in expected], integrity


def new_report_provenance(
    *, model: str, source_summary_path: str, evidence_count: int
) -> ReportProvenance:
    return ReportProvenance(
        model=model,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_summary_path=source_summary_path,
        evidence_count=evidence_count,
        report_schema_version=REPORT_SCHEMA_VERSION,
        human_review_required=True,
    )


def report_from_draft(
    draft: DamageReportDraft,
    *,
    request: DamageReportRequest,
    model: str,
) -> DamageReport:
    ordered, integrity = validate_and_order_findings(request.identities(), draft.findings)
    return DamageReport(
        **draft.model_dump(exclude={"findings"}),
        findings=ordered,
        report_schema_version=REPORT_SCHEMA_VERSION,
        review_status="pending_human_review",
        human_review=HumanReview(),
        integrity=integrity,
        provenance=new_report_provenance(
            model=model,
            source_summary_path=request.source_summary_path,
            evidence_count=len(request.findings),
        ),
    )


def validate_report_for_confirmation(
    report: DamageReport,
    *,
    expected_identities: list[tuple[str, int]],
) -> DamageReport:
    if report.report_schema_version != REPORT_SCHEMA_VERSION:
        raise ValueError("旧版报告不能确认，请重新生成 damage-report.v3")
    ordered, integrity = validate_and_order_findings(expected_identities, report.findings)
    reviewer = report.human_review.reviewer.strip()
    if not reviewer or reviewer.casefold() in {"待指定", "未填写", "未填寫", "unknown", "n/a"}:
        raise ValueError("确认前必须填写真实复核人")
    if any(item.damage_level == "undetermined" for item in ordered):
        raise ValueError("仍有待判定损伤项，不能确认辅助判断")
    report.findings = ordered
    report.integrity = integrity
    return report
