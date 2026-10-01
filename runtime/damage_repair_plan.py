"""Build scale-free, fact-only inputs for AI construction-plan generation."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from runtime.damage_report_schema import DamageReport, REPORT_SCHEMA_VERSION


REPAIR_PLAN_VERSION = "repair-method.v2"
SITE_REVIEW_QUANTITY_BASIS = "現場複核的實體範圍、邊界與工程量"
SCALE_LIMITATION = "未提供可獨立驗證的物理尺度，不得由像素推算實體工程量。"


@dataclass(frozen=True)
class RepairMethodRule:
    method_id: str
    method_name: str
    display_name: str
    damage_types: tuple[str, ...]
    repair_method: str
    required_site_verification: tuple[str, ...]
    upgrade_conditions: tuple[str, ...]
    excluded_conclusions: tuple[str, ...]
    stop_work_conditions: tuple[str, ...]
    code_references: tuple[str, ...] = ()
    quantity_basis: str = SITE_REVIEW_QUANTITY_BASIS


@dataclass(frozen=True)
class RepairPlanLine:
    repair_item_id: str
    finding_id: str
    image_name: str
    finding_index: int
    class_name: str
    severity_level: str
    method_id: str
    method_name: str
    method_display_name: str
    repair_method: str
    decision_status: str
    plan_scope: str
    quantity_basis: str
    component_area_ratio: float | None
    physical_area_mm2: float | None
    required_site_verification: list[str]
    upgrade_conditions: list[str]
    excluded_conclusions: list[str]
    stop_work_conditions: list[str]
    code_references: list[str]
    report_damage_level_reason: str
    report_standards_basis: list[dict[str, Any]]
    report_visual_basis: list[str]
    report_observed_evidence: str
    report_risk_interpretation: str
    report_recommended_action: str
    report_uncertainty: str
    original_image_path: str | None
    annotated_image_path: str | None
    construction_image_path: str | None
    assumptions: str
    review_required: bool = True


LEGACY_REPAIR_METHOD_RULES: tuple[RepairMethodRule, ...] = (
    RepairMethodRule(
        method_id="RC-R01",
        method_name="concrete_patch_repair",
        display_name="混凝土局部修補",
        damage_types=("Concrete crushing", "Delamination"),
        repair_method="受控剔除鬆動或劣化混凝土，完成界面處理、相容材料修補及養護",
        required_site_verification=("敲擊確認鬆動與空鼓邊界", "複核基材強度、含水狀態及損傷深度"),
        upgrade_conditions=("損傷延伸至主要受力區", "發現持續壓碎、支承異常或承載疑慮"),
        excluded_conclusions=("不得僅依影像判定承載力", "不得由像素推算剔除體積或材料用量"),
        stop_work_conditions=("剔除後損傷範圍明顯超出已批准邊界", "發現鋼筋、預應力筋或支承異常"),
    ),
    RepairMethodRule(
        method_id="RC-C02",
        method_name="crack_repair",
        display_name="裂縫封閉或灌注",
        damage_types=("Microcrack", "Structural crack"),
        repair_method="複測裂縫活動性後，由工程師確認採用表面封閉或低壓灌注工法",
        required_site_verification=("複測裂縫位置、走向、寬度、深度及活動性", "核查裂縫與受力區、接縫及滲水路徑的關係"),
        upgrade_conditions=("裂縫持續發展或貫穿構件", "伴隨變形、異響、滲漏或受力異常"),
        excluded_conclusions=("不得僅依外觀判定裂縫成因或結構安全性", "不得由影像估算裂縫實體尺寸"),
        stop_work_conditions=("裂縫活動性或現場受力狀態未查明", "發現疑似承載異常或裂縫持續發展"),
    ),
    RepairMethodRule(
        method_id="RC-P01",
        method_name="spalling_section_repair",
        display_name="剝落區截面修復",
        damage_types=("Minor spalling", "Moderate spalling"),
        repair_method="隔離風險區，受控清除鬆動層，完成基層處理、截面修復及養護",
        required_site_verification=("敲擊確認空鼓與剝落邊界", "複核實體深度、鋼筋暴露及掉落風險"),
        upgrade_conditions=("暴露鋼筋存在明顯鏽蝕或截面損失", "剝落範圍擴大或伴隨異常裂縫"),
        excluded_conclusions=("不得由可見剝落面積推定構件損失率", "不得預設材料用量"),
        stop_work_conditions=("受控剔除時損傷持續擴展", "發現鋼筋、錨固或受力異常"),
    ),
    RepairMethodRule(
        method_id="RC-S01",
        method_name="rebar_corrosion_repair",
        display_name="鋼筋鏽蝕與保護層修復",
        damage_types=("Rebar corrosion",),
        repair_method="複核鋼筋狀態後，按批准範圍除鏽、防護並恢復混凝土保護層",
        required_site_verification=("量測鋼筋截面狀態與鏽蝕範圍", "核查保護層、含水狀態與腐蝕環境"),
        upgrade_conditions=("鋼筋明顯截面損失、斷裂或錨固異常", "腐蝕涉及主要受力鋼筋或大範圍構件"),
        excluded_conclusions=("不得僅依鏽跡判定剩餘承載力", "不得預設補筋或加固方案"),
        stop_work_conditions=("發現鋼筋明顯截面損失、斷裂或錨固異常", "修補範圍涉及未查明的主要受力鋼筋"),
    ),
    RepairMethodRule(
        method_id="RC-U01",
        method_name="engineering_hold",
        display_name="暫停施工並專項評估",
        damage_types=("Structural deformation",),
        repair_method="暫不設定施工修復工法；隔離風險區並由工程師完成專項檢測與評估",
        required_site_verification=("建立測量基準並複核幾何、支承、荷載與發展趨勢", "確認損傷類型、範圍及風險控制措施"),
        upgrade_conditions=("任何持續變形、承載異常、支承失效或證據不一致"),
        excluded_conclusions=("不得將表面處理視為結構問題已消除", "不得在專項評估前設定加固材料與工程量"),
        stop_work_conditions=("未取得工程師批准的專項處置方案", "現場存在持續變形、掉落或承載風險"),
    ),
)


def _report_payload(report: Any) -> dict[str, Any]:
    if hasattr(report, "model_dump"):
        return dict(report.model_dump(mode="json"))
    if isinstance(report, dict):
        nested = report.get("report")
        return dict(nested) if isinstance(nested, dict) else dict(report)
    raise TypeError("report must be a validated model or mapping")


def _confirmed_report(report: Any) -> DamageReport:
    validated = DamageReport.model_validate(_report_payload(report))
    if validated.report_schema_version != REPORT_SCHEMA_VERSION:
        raise ValueError("施工方案僅接受 damage-report.v3 報告")
    if validated.review_status != "confirmed_by_human":
        raise ValueError("損傷報告尚未人工确认，不能建立修復工法")
    review = validated.human_review
    if review.status != "confirmed_by_human" or not review.reviewed_at or not review.reviewer.strip():
        raise ValueError("損傷報告缺少有效人工複核記錄")
    if not validated.integrity.correspondence_valid or validated.integrity.issues:
        raise ValueError("損傷報告證據對應關係無效")
    if any(item.damage_level == "undetermined" for item in validated.findings):
        raise ValueError("損傷報告仍有待判定條目，不能建立修復工法")
    return validated


class DamageRepairPlanner:
    def __init__(self, rules: Iterable[RepairMethodRule] = LEGACY_REPAIR_METHOD_RULES) -> None:
        self.rules = tuple(rules)
        self.rules_by_damage = {
            damage_type.casefold(): rule
            for rule in self.rules
            for damage_type in rule.damage_types
        }

    @staticmethod
    def _image_evidence(image_results: Iterable[dict[str, Any]]) -> tuple[dict[tuple[str, int], dict[str, Any]], list[tuple[str, int]]]:
        evidence: dict[tuple[str, int], dict[str, Any]] = {}
        identities: list[tuple[str, int]] = []
        for result in image_results:
            image_name = str(result.get("image_name", ""))
            for finding in result.get("damage_findings", []) or []:
                identity = (image_name, int(finding.get("index", 0)))
                identities.append(identity)
                evidence[identity] = {
                    "original_image_path": str(result.get("image_path", "") or "") or None,
                    "annotated_image_path": str(result.get("overlay_path", "") or "") or None,
                    "construction_image_path": str(result.get("construction_image_path", "") or "") or None,
                }
        return evidence, identities

    def build_summary(
        self,
        image_results: Iterable[dict[str, Any]],
        *,
        report: Any,
    ) -> dict[str, Any]:
        validated = _confirmed_report(report)
        results = list(image_results)
        evidence, actual_identities = self._image_evidence(results)
        expected_identities = [(item.image_name, item.finding_index) for item in validated.findings]
        expected_counts = Counter(expected_identities)
        actual_counts = Counter(actual_identities)
        issues: list[str] = []
        for identity, count in expected_counts.items():
            actual = actual_counts.get(identity, 0)
            if actual < count:
                issues.append(f"missing_detection:{identity[0]}#{identity[1]}")
            elif actual > count:
                issues.append(f"duplicate_detection:{identity[0]}#{identity[1]}")
        for identity in actual_counts.keys() - expected_counts.keys():
            issues.append(f"unmatched_detection:{identity[0]}#{identity[1]}")

        lines: list[RepairPlanLine] = []
        for finding in validated.findings:
            high_risk = finding.damage_level in {"high", "critical"} or (
                finding.damage_type.casefold() not in self.rules_by_damage
            )
            decision_status = "hold" if high_risk else "draft_for_engineer_review"
            identity = (finding.image_name, finding.finding_index)
            paths = evidence.get(identity, {})
            item_id = f"{finding.image_name}#{finding.finding_index}"
            lines.append(
                RepairPlanLine(
                    repair_item_id=item_id,
                    finding_id=item_id,
                    image_name=finding.image_name,
                    finding_index=finding.finding_index,
                    class_name=finding.damage_type,
                    severity_level=finding.damage_level,
                    method_id="",
                    method_name="",
                    method_display_name="",
                    repair_method="",
                    decision_status=decision_status,
                    plan_scope="",
                    quantity_basis=SITE_REVIEW_QUANTITY_BASIS,
                    component_area_ratio=None,
                    physical_area_mm2=None,
                    required_site_verification=[],
                    upgrade_conditions=[],
                    excluded_conclusions=[],
                    stop_work_conditions=[],
                    code_references=[],
                    report_damage_level_reason=finding.level_reason,
                    report_standards_basis=[item.model_dump(mode="json") for item in finding.standards_basis],
                    report_visual_basis=list(finding.visual_basis),
                    report_observed_evidence=finding.observed_evidence,
                    report_risk_interpretation=finding.risk_interpretation,
                    report_recommended_action=finding.recommended_action,
                    report_uncertainty=finding.uncertainty,
                    original_image_path=paths.get("original_image_path"),
                    annotated_image_path=paths.get("annotated_image_path"),
                    construction_image_path=paths.get("construction_image_path"),
                    assumptions=SCALE_LIMITATION,
                    review_required=True,
                )
            )

        return {
            "repair_plan_version": REPAIR_PLAN_VERSION,
            "source": "confirmed_damage_report",
            "scale_status": "no_verified_physical_scale",
            "image_count": len(results),
            "finding_count": len(lines),
            "expected_evidence_count": len(expected_identities),
            "actual_evidence_count": len(actual_identities),
            "evidence_consistency_status": "consistent" if not issues else "inconsistent",
            "evidence_consistency_note": "證據身份逐項一致" if not issues else "；".join(issues),
            "unknown_classes": sorted(
                {
                    line.class_name
                    for line in lines
                    if line.class_name.casefold() not in self.rules_by_damage
                }
            ),
            "lines": [asdict(line) for line in lines],
            "rules": [],
        }

    @staticmethod
    def build_targeted_render_selection(
        image_results: Iterable[dict[str, Any]], plan_lines: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        lines_by_image: dict[str, list[dict[str, Any]]] = {}
        for line in plan_lines:
            lines_by_image.setdefault(str(line.get("image_name", "")), []).append(dict(line))
        items: list[tuple[str, str]] = []
        skipped_no_detection = skipped_failed = skipped_without_plan = 0
        for result in image_results:
            status = str(result.get("status", ""))
            findings = list(result.get("damage_findings", []) or [])
            if status == "failed":
                skipped_failed += 1
                continue
            if status != "success" or not findings:
                skipped_no_detection += 1
                continue
            matching = lines_by_image.get(str(result.get("image_name", "")), [])
            if not matching:
                skipped_without_plan += 1
                continue
            methods = []
            for line in matching:
                method = str(line.get("repair_method", "")).strip()
                name = str(line.get("method_display_name", "")).strip()
                if method and name:
                    methods.append(f"{name}：{method}")
            if not methods:
                skipped_without_plan += 1
                continue
            items.append((str(result.get("image_path", "")), "；".join(methods)))
        return {
            "items": items,
            "render_count": len(items),
            "skipped_no_detection": skipped_no_detection,
            "skipped_failed": skipped_failed,
            "skipped_without_plan": skipped_without_plan,
        }

    @staticmethod
    def build_reviewed_render_selection(
        image_results: Iterable[dict[str, Any]], plan_lines: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a render queue from reviewed plan work items.

        Once the engineer has enabled rendering, detection metadata is
        diagnostic only. A reviewed work item must reach the renderer even if
        its detection result is missing or failed; the renderer can then
        return an auditable failure for an unavailable source/provider.
        """
        results_by_name = {
            str(result.get("image_name", "")): dict(result)
            for result in image_results
        }
        items: list[tuple[str, str]] = []
        missing_source = 0
        for line in plan_lines:
            reviewed = dict(line)
            image_name = str(reviewed.get("image_name", "")).strip()
            result = results_by_name.get(image_name, {})
            source = str(
                reviewed.get("original_image_path")
                or result.get("image_path")
                or ""
            ).strip()
            if not source:
                missing_source += 1
            method_name = str(
                reviewed.get("method_display_name")
                or reviewed.get("method_name")
                or "已审核施工工项"
            ).strip()
            repair_method = str(
                reviewed.get("repair_method")
                or reviewed.get("reviewed_method")
                or "按已审核施工方案进行现场复核后实施"
            ).strip()
            items.append((source, f"{method_name}：{repair_method}"))
        return {
            "items": items,
            "render_count": len(items),
            "skipped_no_detection": 0,
            "skipped_failed": 0,
            "skipped_without_plan": 0,
            "missing_source_path": missing_source,
        }

    @staticmethod
    def save_summary(summary: dict[str, Any], path: str | Path) -> Path:
        target = Path(path)
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
