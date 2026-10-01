"""Deterministic project-context controls derived only from explicit overview terms."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectContextControls:
    report_notes: list[str] = field(default_factory=list)
    preconstruction_checks: list[str] = field(default_factory=list)
    safety_controls: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def analyze_project_overview(overview: str) -> ProjectContextControls:
    text = str(overview or "").strip()
    report_notes: list[str] = []
    checks: list[str] = []
    safety: list[str] = []
    if not text:
        return ProjectContextControls()
    if any(term in text for term in ("地下室", "地下空间", "地下工程")):
        report_notes.append("项目概括明确对象涉及地下空间，损伤解释与处置建议需结合通风、照明、排水或渗水、疏散和作业面条件")
        checks.append("依据项目概括复核地下空间的通风、照明、排水或渗水、疏散和作业面条件")
        safety.append("地下空间施工期间保持安全疏散通道、通风和照明，不得因修复作业阻断既有安全设施")
    if any(term in text for term in ("停车库", "地下车库", "车库")):
        report_notes.append("项目概括明确包含停车库，现场复核与修复组织需考虑车辆通行、停放、限高和分区隔离")
        checks.append("依据项目概括核实施工区域的车辆通行、停放、限高和分区隔离条件")
        safety.append("停车库修复作业应与车辆和人员通行分离，并保护既有交通、消防和机电设施")
    if "设备机房" in text or "机房" in text:
        report_notes.append("项目概括明确包含设备机房，处置建议需考虑设备运行、停机接口以及防尘、防水和振动影响")
        checks.append("依据项目概括核实设备机房运行状态、停机条件以及设备防尘、防水和振动影响")
        safety.append("设备机房邻近作业不得污染、浸水、碰撞或擅自停用在运行设备")
    if "防火" in text or "消防" in text:
        report_notes.append("项目概括明确涉及防火区域，修复不得破坏防火分区、消防设施或既有防火封堵")
        checks.append("依据项目概括核对防火分区、消防设施、防火封堵和动火管理要求")
        safety.append("不得破坏防火分区完整性或遮挡、停用消防设施；涉及动火时须执行审批和监护")
    if any(term in text for term in ("荷载传递", "空间支撑", "主体结构", "抗震")):
        report_notes.append("项目概括明确地下室承担主体结构荷载传递、空间支撑或抗震作用，相关损伤必须结合结构作用进行工程师复核")
        checks.append("依据项目概括复核构件在主体结构荷载传递、空间支撑和抗震体系中的作用")
        safety.append("修复前不得擅自削弱、切断或改变主体结构的荷载传递、空间支撑和抗震构造")
    if any(term in text for term in ("居民楼", "住宅", "小区")):
        report_notes.append("项目概括明确属于在用居住建筑场景，现场复核和施工建议需兼顾居民通行、疏散、噪声与粉尘控制")
        checks.append("依据项目概括确认居民使用期间的施工时段、噪声粉尘控制和通行组织")
        safety.append("在用居住建筑内作业时应隔离施工区并保障居民通行、疏散和既有设施正常使用")
    return ProjectContextControls(
        report_notes=report_notes,
        preconstruction_checks=checks,
        safety_controls=safety,
        limitations=["项目概括用于项目背景和工程约束，不能替代现场复测、设计资料或结构验算"],
    )
