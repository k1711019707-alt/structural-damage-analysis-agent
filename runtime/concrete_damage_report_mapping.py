"""Scale-free deterministic values for the managed concrete-damage DOCX template."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


NOT_PROVIDED = "未提供"
NOT_DETECTED = "未检出"
NOT_APPLICABLE = "不适用（无尺度换算）"

_SEVERITY_ORDER = {"undetermined": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_SEVERITY_NAMES = {
    "undetermined": "待判定",
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "危急",
}
_CATEGORY_DEFINITIONS = (
    ("f1", "non_structural", "非结构裂缝", ("microcrack", "non structural crack", "non-structural crack", "非结构裂缝", "微裂缝")),
    ("f2", "structural", "结构裂缝", ("structural crack", "结构裂缝")),
    ("f3", "minor_spalling", "轻微剥落", ("minor spalling", "轻微剥落")),
    ("f4", "moderate_spalling", "中度剥落", ("moderate spalling", "中度剥落")),
    ("f5", "major_spalling", "严重剥落/脱层", ("major spalling", "severe spalling", "delamination", "严重剥落", "脱层")),
    ("f6", "rebar_damage", "钢筋腐蚀/钢筋暴露", ("rebar corrosion", "rebar exposure", "steel corrosion", "钢筋腐蚀", "露筋")),
    ("f7", "concrete_crushing", "混凝土压碎", ("concrete crushing", "混凝土压碎")),
    ("f8", "deformation", "结构变形", ("deformation", "structural deformation", "结构变形")),
)


def _text(value: Any, default: str = NOT_PROVIDED) -> str:
    text = str(value or "").strip()
    return text or default


def _report_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    nested = raw.get("report")
    return dict(nested) if isinstance(nested, dict) else raw


def _normalise_label(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _category_key(damage_type: Any) -> str | None:
    label = _normalise_label(damage_type)
    for _, key, _, aliases in _CATEGORY_DEFINITIONS:
        if label in {_normalise_label(alias) for alias in aliases}:
            return key
    return None


def _highest_level(findings: list[dict[str, Any]]) -> str:
    level = "undetermined"
    for finding in findings:
        candidate = str(finding.get("damage_level") or "undetermined").lower()
        if _SEVERITY_ORDER.get(candidate, 0) > _SEVERITY_ORDER[level]:
            level = candidate
    return level


def _level_label(level: str, *, confirmed: bool) -> str:
    suffix = "AI辅助判断，已人工确认" if confirmed else "AI辅助判断，待人工复核"
    return f"{_SEVERITY_NAMES.get(level, '待判定')}（{suffix}）"


def _warning(level: str) -> str:
    return "是" if level in {"high", "critical"} else "否"


def _category_values(
    prefix: str,
    findings: list[dict[str, Any]],
    *,
    confirmed: bool,
    has_width: bool,
) -> dict[str, str]:
    if not findings:
        values = {
            f"{prefix}n": "0",
            f"{prefix}a": NOT_APPLICABLE,
            f"{prefix}g": NOT_DETECTED,
            f"{prefix}x": "否",
        }
    else:
        level = _highest_level(findings)
        values = {
            f"{prefix}n": str(len(findings)),
            f"{prefix}a": NOT_APPLICABLE,
            f"{prefix}g": _level_label(level, confirmed=confirmed),
            f"{prefix}x": _warning(level),
        }
    if has_width:
        values[f"{prefix}w"] = NOT_APPLICABLE
        values[f"{prefix}l"] = NOT_APPLICABLE
    else:
        values[f"{prefix}r"] = NOT_APPLICABLE
    return values


def _recommendations(report: dict[str, Any]) -> str:
    actions: list[str] = []
    for finding in report.get("findings") or []:
        action = _text((finding or {}).get("recommended_action"), default="")
        if action and action not in actions:
            actions.append(action)
    return "；".join(actions) if actions else "由专业工程师结合现场尺度、构件资料和检测规范复核后确定处置。"


def build_concrete_damage_template_values(
    summary: dict[str, Any] | None,
    report_payload: dict[str, Any] | None,
) -> dict[str, str]:
    """Map only reviewed report semantics; never convert detector pixels into quantities."""
    summary = dict(summary or {})
    report = _report_payload(report_payload)
    subject = dict(report.get("subject") or {})
    findings = [dict(item) for item in report.get("findings") or [] if isinstance(item, dict)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        category = _category_key(finding.get("damage_type"))
        if category:
            grouped[category].append(finding)

    confirmed = (
        report.get("report_schema_version") == "damage-report.v3"
        and report.get("review_status") == "confirmed_by_human"
        and dict(report.get("human_review") or {}).get("status") == "confirmed_by_human"
    )
    overall_level = str(report.get("overall_screening_level") or _highest_level(findings))
    overall_label = _level_label(overall_level, confirmed=confirmed)
    successful_images = [item for item in summary.get("results") or [] if item.get("status") == "success"]
    component_id = _text(subject.get("asset_id"))
    component_name = _text(subject.get("component"))
    inspection_date = _text(subject.get("inspection_time"))
    overview = _text(subject.get("project_overview") or summary.get("project_overview"))
    crack_findings = grouped["non_structural"] + grouped["structural"]
    defect_findings = (
        grouped["minor_spalling"]
        + grouped["moderate_spalling"]
        + grouped["major_spalling"]
        + grouped["rebar_damage"]
        + grouped["concrete_crushing"]
    )
    deformation_findings = grouped["deformation"]
    detected_labels = [label for _, key, label, _ in _CATEGORY_DEFINITIONS if grouped[key]]
    limitations = [str(item).strip() for item in report.get("limitations") or [] if str(item).strip()]
    conclusion = _text(report.get("executive_summary"), default="本报告依据视觉证据和结构化识别提示生成，须由专业工程师复核。")
    conclusion += f" 共包含 {len(findings)} 项报告损伤；综合等级为{overall_label}。"
    conclusion += " 未提供可靠物理尺度，面积、占比、宽度和长度均标记为不适用，不能从像素换算实体工程量。"

    def category_level(key: str) -> str:
        return _level_label(_highest_level(grouped[key]), confirmed=confirmed) if grouped[key] else NOT_DETECTED

    values = {
        "project_name": _text(subject.get("project_name")),
        "project_overview": overview,
        "component_name": component_name,
        "component_id": component_id,
        "component_location": NOT_PROVIDED,
        "component_type": NOT_PROVIDED,
        "dimensions": NOT_PROVIDED,
        "concrete_grade": NOT_PROVIDED,
        "cover_thickness": NOT_PROVIDED,
        "environment_class": NOT_PROVIDED,
        "component_importance": NOT_PROVIDED,
        "report_number": "自动生成（待编号）",
        "inspection_unit": NOT_PROVIDED,
        "inspection_date": inspection_date,
        "image_count_and_view": f"{len(successful_images)} 张图像 / {len(successful_images)} 个视角（按文件计）",
        "c11": component_id,
        "c12": NOT_PROVIDED,
        "c13": NOT_PROVIDED,
        "c14": str(len(successful_images)),
        "c15": overall_label,
        "c21": NOT_APPLICABLE,
        "c22": NOT_APPLICABLE,
        "c23": NOT_APPLICABLE,
        "c24": NOT_APPLICABLE,
        "c25": NOT_APPLICABLE,
        "finding_summary": "、".join(detected_labels) if detected_labels else NOT_DETECTED,
        "maximum_width": NOT_APPLICABLE,
        "area_ratio": NOT_APPLICABLE,
        "assessment_level": overall_label,
        "conclusion_text": conclusion,
        "recommendation_text": _recommendations(report),
        "other_recommendations": "；".join(limitations) if limitations else "未提供现场尺度、构件设计资料或专项检测结论",
        "crack_types": "、".join(label for _, key, label, _ in _CATEGORY_DEFINITIONS[:2] if grouped[key]) or NOT_DETECTED,
        "crack_repair_method": "由专业工程师结合现场复核确定封闭、灌注或其他修复工法",
        "surface_damage_types": "、".join(label for _, key, label, _ in _CATEGORY_DEFINITIONS[2:7] if grouped[key]) or NOT_DETECTED,
        "surface_repair_method": "由专业工程师结合现场复核确定除锈、防护和局部修补工法",
        "non_structural_crack_type": "非结构裂缝" if grouped["non_structural"] else NOT_DETECTED,
        "non_structural_repair_method": "表面封闭或局部修补，现场复核后实施",
        "non_structural_max_width": NOT_APPLICABLE,
        "non_structural_assessment_level": category_level("non_structural"),
        "non_structural_warning": _warning(_highest_level(grouped["non_structural"])),
        "structural_max_width": NOT_APPLICABLE,
        "structural_assessment_level": category_level("structural"),
        "structural_warning": _warning(_highest_level(grouped["structural"])),
        "minor_spalling_area": NOT_APPLICABLE,
        "minor_spalling_ratio": NOT_APPLICABLE,
        "minor_spalling_level": category_level("minor_spalling"),
        "moderate_spalling_area": NOT_APPLICABLE,
        "moderate_spalling_ratio": NOT_APPLICABLE,
        "moderate_spalling_level": category_level("moderate_spalling"),
        "major_spalling_area": NOT_APPLICABLE,
        "major_spalling_ratio": NOT_APPLICABLE,
        "major_spalling_level": category_level("major_spalling"),
        "rebar_damage_area": NOT_APPLICABLE,
        "rebar_damage_ratio": NOT_APPLICABLE,
        "rebar_damage_level": category_level("rebar_damage"),
        "concrete_crushing_area": NOT_APPLICABLE,
        "concrete_crushing_ratio": NOT_APPLICABLE,
        "concrete_crushing_level": category_level("concrete_crushing"),
        "deformation_ratio": NOT_APPLICABLE,
        "deformation_level": category_level("deformation"),
        "deformation_warning": _warning(_highest_level(deformation_findings)),
        "crack": _level_label(_highest_level(crack_findings), confirmed=confirmed) if crack_findings else NOT_DETECTED,
        "defect": _level_label(_highest_level(defect_findings), confirmed=confirmed) if defect_findings else NOT_DETECTED,
        "deform": _level_label(_highest_level(deformation_findings), confirmed=confirmed) if deformation_findings else NOT_DETECTED,
        "overall": overall_label,
        "warn": "存在" if overall_level in {"high", "critical"} else "不存在",
    }
    for prefix, key, _, _ in _CATEGORY_DEFINITIONS:
        values.update(
            _category_values(
                prefix,
                grouped[key],
                confirmed=confirmed,
                has_width=prefix in {"f1", "f2"},
            )
        )
    return values
