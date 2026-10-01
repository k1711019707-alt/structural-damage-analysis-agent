"""Dependency-free normalization for user-facing generated Chinese text."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, TypeVar


# Keep the table local to the application so frozen builds do not need an
# external OpenCC data file. Pairs cover the engineering/report vocabulary
# emitted by the remote prompts and local repair fallback.
_PAIRS = (
    ("\u50c5", "\u4ec5"), ("\u640d", "\u635f"), ("\u50b7", "\u4f24"),
    ("\u7bc4", "\u8303"), ("\u570d", "\u56f4"), ("\u72c0", "\u72b6"),
    ("\u614b", "\u6001"), ("\u8207", "\u4e0e"), ("\u8907", "\u590d"),
    ("\u73fe", "\u73b0"), ("\u61c9", "\u5e94"),
    ("\u689d", "\u6761"),
    ("\u7d1a", "\u7ea7"),
    ("\u6e2c", "\u6d4b"), ("\u5be6", "\u5b9e"), ("\u9ad4", "\u4f53"),
    ("\u69cb", "\u6784"), ("\u5834", "\u573a"), ("\u5831", "\u62a5"),
    ("\u8b49", "\u8bc1"), ("\u64da", "\u636e"), ("\u78ba", "\u786e"),
    ("\u8a8d", "\u8ba4"), ("\u7dad", "\u7ef4"), ("\u8b77", "\u62a4"),
    ("\u53c3", "\u53c2"), ("\u7e8c", "\u7eed"), ("\u7522", "\u4ea7"),
    ("\u9069", "\u9002"), ("\u9810", "\u9884"), ("\u8a2d", "\u8bbe"),
    ("\u63a1", "\u91c7"), ("\u7d93", "\u7ecf"), ("\u6e96", "\u51c6"),
    ("\u990a", "\u517b"), ("\u8a18", "\u8bb0"), ("\u9304", "\u5f55"),
    ("\u5c0d", "\u5bf9"), ("\u7e2b", "\u7f1d"), ("\u5bec", "\u5bbd"),
    ("\u52d5", "\u52a8"), ("\u6a19", "\u6807"), ("\u7de8", "\u7f16"),
    ("\u865f", "\u53f7"), ("\u5c64", "\u5c42"), ("\u85dd", "\u827a"),
    ("\u767c", "\u53d1"), ("\u6ef2", "\u6e17"), ("\u812b", "\u8131"),
    ("\u92fc", "\u94a2"), ("\u93fd", "\u9508"), ("\u8755", "\u8680"),
    ("\u5c08", "\u4e13"), ("\u9805", "\u9879"), ("\u6aa2", "\u68c0"),
    ("\u8a55", "\u8bc4"), ("\u5e7e", "\u51e0"), ("\u8f09", "\u8f7d"),
    ("\u8da8", "\u8d8b"), ("\u52e2", "\u52bf"), ("\u76e3", "\u76d1"),
    ("\u8655", "\u5904"), ("\u66ab", "\u6682"), ("\u98a8", "\u98ce"),
    ("\u96aa", "\u9669"), ("\u5340", "\u533a"), ("\u5c07", "\u5c06"),
    ("\u5e2b", "\u5e08"), ("\u842c", "\u4e07"), ("\u8208", "\u5174"),
    ("\u8209", "\u4e3e"), ("\u820a", "\u65e7"), ("\u88dd", "\u88c5"),
    ("\u898f", "\u89c4"), ("\u8996", "\u89c6"), ("\u89ba", "\u89c9"),
    ("\u8a02", "\u8ba2"), ("\u8a08", "\u8ba1"), ("\u8a0a", "\u8baf"),
    ("\u8a13", "\u8bad"), ("\u8a23", "\u8bc0"), ("\u8a31", "\u8bb8"),
    ("\u8a34", "\u8bc9"), ("\u8a3a", "\u8bca"), ("\u8a3c", "\u8bc1"),
    ("\u8a50", "\u8bc8"), ("\u8a5e", "\u8bcd"), ("\u8a73", "\u8be6"),
    ("\u8a71", "\u8bdd"), ("\u8aa0", "\u8bda"), ("\u8aa4", "\u8bef"),
    ("\u8aaa", "\u8bf4"), ("\u8acb", "\u8bf7"), ("\u8ab2", "\u8bfe"),
    ("\u8abf", "\u8c03"), ("\u8ac7", "\u8c08"), ("\u8b39", "\u8c28"),
    ("\u8b58", "\u8bc6"), ("\u8b5c", "\u8c31"), ("\u8b8a", "\u53d8"),
    ("\u8b93", "\u8ba9"), ("\u8c50", "\u4e30"), ("\u8ca0", "\u8d1f"),
    ("\u8ca1", "\u8d22"), ("\u8ca2", "\u8d21"), ("\u8cc7", "\u8d44"),
    ("\u8cd3", "\u5bbe"), ("\u8ce3", "\u5356"), ("\u8fa6", "\u529e"),
    ("\u8f2f", "\u8f91"), ("\u8fb2", "\u519c"), ("\u9019", "\u8fd9"),
    ("\u9032", "\u8fdb"), ("\u904b", "\u8fd0"), ("\u904e", "\u8fc7"),
    ("\u9084", "\u8fd8"), ("\u908a", "\u8fb9"), ("\u91ab", "\u533b"),
    ("\u91cb", "\u91ca"), ("\u91dd", "\u9488"), ("\u932f", "\u9519"),
    ("\u9375", "\u952e"), ("\u93e1", "\u955c"), ("\u9577", "\u957f"),
    ("\u9580", "\u95e8"), ("\u9583", "\u95ea"), ("\u9589", "\u95ed"),
    ("\u958b", "\u5f00"), ("\u9593", "\u95f4"), ("\u95dc", "\u5173"),
    ("\u968e", "\u9636"), ("\u968f", "\u968f"), ("\u96e3", "\u96be"),
    ("\u96dc", "\u6742"), ("\u96fb", "\u7535"), ("\u9748", "\u7075"),
    ("\u975c", "\u9759"), ("\u9867", "\u987e"), ("\u9928", "\u9986"),
    ("\u9aee", "\u53d1"), ("\u9b25", "\u6597"), ("\u9b27", "\u95f9"),
    ("\u9b06", "\u677e"), ("\u9b5a", "\u9c7c"), ("\u9ce5", "\u9e1f"),
    ("\u9e7d", "\u76d0"), ("\u9e97", "\u4e3d"), ("\u9ea5", "\u9ea6"),
    ("\u9ec3", "\u9ec4"), ("\u9ee8", "\u515a"), ("\u9ede", "\u70b9"),
    ("\u9f8d", "\u9f99"), ("\u50be", "\u503e"), ("\u5be9", "\u5ba1"),
    ("\u9a57", "\u9a8c"), ("\u7e3d", "\u603b"), ("\u5167", "\u5185"),
    ("\u9078", "\u9009"), ("\u5eab", "\u5e93"), ("\u5716", "\u56fe"),
    ("\u9801", "\u9875"), ("\u6642", "\u65f6"), ("\u7b49", "\u7b49"), ("\u8a18", "\u8bb0"),
)
_TRADITIONAL_TO_SIMPLIFIED = str.maketrans(dict(_PAIRS))

_PROTECTED_KEYS = {
    "image_name", "image_path", "overlay_path", "original_image_path",
    "annotated_image_path", "construction_image_path", "source_marker",
    "document_id", "finding_id", "repair_item_id", "work_item_id", "method_id",
    "damage_level", "review_status", "plan_status", "decision_status",
    "repair_method_source", "figure_type", "availability", "generated_at",
    "source_summary_path", "source_report_hash", "repair_plan_hash",
    "settings_snapshot_id", "report_schema_version", "plan_schema_version",
}
_EXACT_PROTECTED_VALUES = {
    "undetermined", "low", "medium", "high", "critical", "remote_ai",
    "local_fallback", "legacy_local", "pending_human_review", "confirmed_by_human",
    "edited_pending_confirmation", "pending_engineer_review", "hold",
    "evidence_inconsistent", "consistent", "inconsistent", "available", "placeholder",
}
_SOURCE_MARKER = re.compile(r"^\[KB:[^:\]\r\n]+:[^\]\r\n]+\]$")
_BRACKETED_REFERENCE = re.compile(r"^\[[^\]\r\n]+\]$")


def simplify_chinese_text(value: str, *, protected: bool = False) -> str:
    text = str(value)
    if (
        protected
        or text in _EXACT_PROTECTED_VALUES
        or _SOURCE_MARKER.fullmatch(text)
        or _BRACKETED_REFERENCE.fullmatch(text)
    ):
        return text
    return text.translate(_TRADITIONAL_TO_SIMPLIFIED)


def simplify_chinese_value(value: Any, *, key: str | None = None) -> Any:
    protected = str(key or "").casefold() in _PROTECTED_KEYS
    if isinstance(value, str):
        return simplify_chinese_text(value, protected=protected)
    if isinstance(value, list):
        return [simplify_chinese_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return tuple(simplify_chinese_value(item, key=key) for item in value)
    if isinstance(value, Mapping):
        return {
            item_key: simplify_chinese_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    return value


ModelT = TypeVar("ModelT")


def simplify_chinese_model(model: ModelT, model_type: Any | None = None) -> ModelT:
    if not getattr(type(model), "model_fields", None):
        return simplify_chinese_value(model)

    normalized_model = model.model_copy(deep=True)

    def visit(value: Any, *, key: str | None = None) -> Any:
        fields = getattr(type(value), "model_fields", None)
        if fields:
            for field_name in fields:
                setattr(value, field_name, visit(getattr(value, field_name), key=field_name))
            return value
        if isinstance(value, list):
            return [visit(item, key=key) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item, key=key) for item in value)
        if isinstance(value, Mapping):
            return {
                item_key: visit(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, str):
            return simplify_chinese_text(value, protected=str(key or "").casefold() in _PROTECTED_KEYS)
        return value

    result = visit(normalized_model)
    return result
