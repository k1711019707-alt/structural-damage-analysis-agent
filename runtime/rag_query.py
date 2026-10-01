"""Bounded, profile-specific retrieval query construction."""
from __future__ import annotations

import re
from typing import Any


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for key in ("class_name", "damage_type", "repair_method", "component_type", "component_name", "project_name", "project_overview"):
            result.extend(_values(value.get(key)))
        return result
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            result.extend(_values(item))
        return result
    return []


def build_profile_query(profile_name: str, evidence: dict[str, Any], *, max_chars: int = 900) -> str:
    """Create a compact query from engineering fields, never the whole JSON."""
    profile = str(profile_name or "")
    values: list[str] = []
    values.extend(_values(evidence.get("project_overview")))
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else evidence
    values.extend(_values(summary))
    if profile == "施工方案":
        values.extend(["修复施工", "施工前复核", "质量控制", "安全措施", "验收", "停工条件", "修复后复检"])
    else:
        values.extend(["结构损伤", "现场检测", "损伤分析", "工程师复核", "适用条件"])
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            continue
        # Avoid paths, hashes, JSON keys, and long numeric/model metadata.
        if (len(text) > 240 or text.startswith(("{", "[", "E:\\", "C:\\", "D:\\")) or re.fullmatch(r"[0-9a-fA-F]{12,}", text)):
            continue
        if text.casefold() not in seen:
            seen.add(text.casefold())
            cleaned.append(text)
    query = " ".join(cleaned)
    return query[: max(120, int(max_chars))]
