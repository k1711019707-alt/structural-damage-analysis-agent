from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from PIL import Image, ImageOps
from pydantic import ValidationError

from runtime.damage_report_schema import (
    REPORT_SCHEMA_VERSION,
    DamageReport,
    DamageReportDraft,
    DamageReportRequest,
    HumanReview,
    ReportIntegrity,
    ReportFinding,
    finding_identity,
    new_report_provenance,
    report_from_draft,
    validate_and_order_findings,
    validate_report_for_confirmation,
)
from runtime.generation_context import GenerationContext
from runtime.generation_citations import (
    build_citation_catalog,
    normalize_citation_catalog,
    render_knowledge_citations,
)
from runtime.project_context import analyze_project_overview
from runtime.simplified_chinese import simplify_chinese_model, simplify_chinese_value
from runtime.generation_manifest import (
    atomic_write_json,
    atomic_write_text,
    merge_profile_manifest,
    redact_secret_text,
)


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.6-sol"
REPORT_INSTRUCTIONS = """You are a visual inspection assistant for concrete-component damage.
Inspect every attached original image and recognition overlay. Detection hints locate possible
damage but are not an assessment result. Return only JSON matching the supplied draft schema.

For every supplied (image_name, finding_index), return exactly one finding with the same identity.
Do not omit, duplicate, or invent findings. Use only undetermined, low, medium, high, or critical
for damage levels. Explain visible evidence, uncertainty, risk interpretation, recommended checks,
and any standards basis. Knowledge-base excerpts are reference material, not site observations.

All narrative and list fields must be written in Simplified Chinese. Do not mix
Traditional Chinese, English, or another language in user-facing descriptions.
Keep image names, file paths, source markers, standard identifiers, damage-level
enums, and schema/API field names exactly as supplied.

No reliable pixel-to-physical scale is available. Never calculate, quote, infer, or request pixel
area, pixel length, crack width, physical dimensions, area ratio, calibration conversion, or a
numeric assessment threshold. Do not claim a clause number absent from supplied references. Do not
return generation timestamps, model identity, provenance, integrity, or human-review facts. The
result is an AI-assisted draft and must remain pending human review."""
REPORT_INSTRUCTIONS += """

Keep every field concise and non-repetitive. Describe only the evidence and engineering judgment
needed for this finding; do not restate the full report, prompt, or knowledge excerpts. Respect all
schema maxLength and maxItems limits. The application assembles arbitrarily large reports from
independently validated finding responses."""

SEVERITY_ORDER = {"undetermined": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_LABELS = {
    "undetermined": "待判定",
    "low": "低",
    "medium": "中等",
    "high": "高",
    "critical": "严重",
}
DAMAGE_TYPE_LABELS = {
    "Structural crack": "结构裂缝",
    "Microcrack": "微裂缝",
    "Rebar corrosion": "钢筋锈蚀",
    "Minor spalling": "轻微剥落",
    "Moderate spalling": "中等剥落",
    "Delamination": "脱空/分层",
    "Concrete crushing": "混凝土压碎",
    "Structural deformation": "结构变形",
}


def _damage_type_label(value: str) -> str:
    return DAMAGE_TYPE_LABELS.get(str(value).strip(), str(value).strip() or "未知损伤")


def _count_labels(values: Counter[str], labeler: Callable[[str], str]) -> str:
    return "、".join(
        f"{labeler(value)}{count}项"
        for value, count in values.items()
    )


def _build_multi_finding_summary(
    findings: list[ReportFinding],
) -> tuple[str, str]:
    """Summarize validated findings without inventing measurements or safety conclusions."""
    if not findings:
        return (
            "本次未形成可供复核的损伤明细，不能据此给出总体辅助判断。",
            "没有已校验的损伤项，无法按单项等级确定总体辅助等级；需要补充有效检测证据并由工程师复核。",
        )

    type_counts = Counter(_damage_type_label(item.damage_type) for item in findings)
    level_counts = Counter(item.damage_level for item in findings)
    highest_level = max(
        (item.damage_level for item in findings),
        key=lambda level: SEVERITY_ORDER[level],
        default="undetermined",
    )
    highest_items = [
        f"{item.image_name}（{_damage_type_label(item.damage_type)}）"
        for item in findings
        if item.damage_level == highest_level
    ]
    type_text = _count_labels(type_counts, lambda value: value)
    level_text = _count_labels(
        Counter(
            {
                SEVERITY_LABELS[level]: count
                for level, count in level_counts.items()
            }
        ),
        lambda value: value,
    )
    highest_text = "、".join(highest_items[:4])
    if len(highest_items) > 4:
        highest_text += f"等{len(highest_items)}项"

    summary = (
        f"本次共分析{len(findings)}项损伤，涉及{type_text}；"
        f"单项辅助等级分布为{level_text}。"
        f"最高辅助等级为{SEVERITY_LABELS[highest_level]}，对应{highest_text}。"
    )
    if any(
        item.damage_level in {"high", "critical"}
        or item.damage_type.casefold() == "structural deformation".casefold()
        for item in findings
    ):
        summary += "其中高等级或结构变形条目应优先进行现场复核、风险控制和专项工程判断。"
    summary += "以上内容是基于图像和结构化检测证据形成的辅助分析，不替代现场检测、结构验算或安全鉴定。"

    rationale = (
        f"总体辅助等级为{SEVERITY_LABELS[highest_level]}，采用全部{len(findings)}项已校验损伤的"
        "规范化单项等级中的最不利等级确定。"
        f"本次最高等级由{highest_text}的{SEVERITY_LABELS[highest_level]}级辅助判断建立，"
        "不是对其他条目的重新分级。"
        "该等级仅表示视觉证据下的辅助筛查优先级，不等同于结构安全等级、承载力结论或可靠性鉴定；"
        "最终结论必须结合现场复测、必要的专项检测和工程师审核。"
    )
    return summary[:1600], rationale[:1200]


@dataclass(frozen=True)
class VisualBudget:
    max_attachments: int = 16
    max_long_edge: int = 1600
    max_image_bytes: int = 1_500_000
    max_total_bytes: int = 12_000_000
    jpeg_quality: int = 88


@dataclass(frozen=True)
class PreparedVisual:
    image_name: str
    role: str
    source_path: str
    source_width: int
    source_height: int
    source_bytes: int
    sent_width: int
    sent_height: int
    sent_bytes: int
    resized: bool
    data_url: str

    def audit(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("data_url", None)
        return payload


class ReportConfigurationError(ValueError):
    pass


class ReportGenerationError(RuntimeError):
    pass


def _generation_audit(
    *,
    mode: str,
    model: str,
    generation_context: GenerationContext | None,
    fallback_reason: str = "",
) -> dict[str, Any]:
    knowledge_used = bool(generation_context and generation_context.knowledge_base_used)
    knowledge_status = (
        generation_context.knowledge_base_status
        if generation_context is not None
        else ("used" if knowledge_used else "not_used")
    )
    return {
        "generation_mode": mode,
        "model": model,
        "vision_input_used": mode == "remote_vision_ai",
        "knowledge_base_used": knowledge_used,
        "knowledge_base_status": knowledge_status,
        "fallback_reason": redact_secret_text(fallback_reason) if fallback_reason else "",
    }


def _ai_output_changes(request: DamageReportRequest, report: DamageReport) -> dict[str, Any]:
    expected = [(item.image_name, item.index) for item in request.findings]
    actual = [(item.image_name, item.finding_index) for item in report.findings]
    expected_set, actual_set = set(expected), set(actual)
    return {
        "set_changed": expected != actual,
        "input_item_count": len(expected),
        "ai_item_count": len(actual),
        "omitted_input_items": [
            {"image_name": image_name, "finding_index": finding_index}
            for image_name, finding_index in expected_set - actual_set
        ],
        "ai_added_items": [
            {"image_name": image_name, "finding_index": finding_index}
            for image_name, finding_index in actual_set - expected_set
        ],
        "ai_order": [
            {"image_name": image_name, "finding_index": finding_index}
            for image_name, finding_index in actual
        ],
    }


def _response_field(value: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field_name, default)
    return getattr(value, field_name, default)


def extract_response_output_text(response: Any) -> str | None:
    """Return generated text from either Responses SDK output representation."""
    output_text = _response_field(response, "output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    response_output = _response_field(response, "output")
    if not isinstance(response_output, (list, tuple)):
        return None

    text_parts: list[str] = []
    for output_item in response_output:
        content_items = _response_field(output_item, "content")
        if not isinstance(content_items, (list, tuple)):
            continue
        for content_item in content_items:
            if _response_field(content_item, "type") != "output_text":
                continue
            content_text = _response_field(content_item, "text")
            if isinstance(content_text, str) and content_text:
                text_parts.append(content_text)
    return "".join(text_parts) or None


def extract_responses_stream_output_text(
    response: Any,
    *,
    on_text_update: Callable[[str], None] | None = None,
) -> str | None:
    """Return text from a Responses event stream or a complete response object.

    Some OpenAI-compatible gateways ignore ``stream=True`` and return a normal
    Responses object.  Keep accepting that representation while aggregating
    official Responses ``response.output_text.delta`` events in arrival order.
    """
    if not hasattr(response, "__iter__") or hasattr(response, "output_text"):
        text = extract_response_output_text(response)
        if text and on_text_update is not None:
            try:
                on_text_update(text)
            except Exception:
                pass
        return text

    parts: list[str] = []
    for event in response:
        if _response_field(event, "type") != "response.output_text.delta":
            continue
        delta = _response_field(event, "delta")
        if isinstance(delta, str):
            parts.append(delta)
            if on_text_update is not None:
                try:
                    on_text_update("".join(parts))
                except Exception:
                    pass
    return "".join(parts) or None


def _make_schema_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Make every object property required for Responses strict-schema providers."""
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["required"] = list(properties)
    for value in schema.values():
        if isinstance(value, dict):
            _make_schema_strict(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _make_schema_strict(item)
    return schema


def _report_findings_json_schema() -> dict[str, Any]:
    finding_schema = ReportFinding.model_json_schema()
    definitions = finding_schema.pop("$defs", {})
    return _make_schema_strict(
        {
            "$schema": "http://json-schema.org/draft-07/schema#",
            **({"$defs": definitions} if definitions else {}),
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": finding_schema,
                    "minItems": 1,
                    "maxItems": 1,
                }
            },
            "required": ["findings"],
            "additionalProperties": False,
        }
    )


def normalize_responses_base_url(value: str | None) -> str:
    """Accept either an API root or a `/v1` root for OpenAI-compatible endpoints."""
    raw = (value or DEFAULT_BASE_URL).strip().rstrip("/")
    if not raw:
        return DEFAULT_BASE_URL
    # Users often paste a full endpoint from provider documentation.
    raw = re.sub(r"/v1/(responses|chat/completions)$", "/v1", raw, flags=re.IGNORECASE)
    if not re.search(r"/v1$", raw, flags=re.IGNORECASE):
        raw = f"{raw}/v1"
    return raw


def _encode_visual(
    *,
    image_name: str,
    role: str,
    path_value: str | Path,
    budget: VisualBudget,
) -> PreparedVisual:
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"视觉模型输入图片不存在：{path}")
    source_bytes = path.stat().st_size
    with Image.open(path) as opened:
        source_width, source_height = opened.size
        image = ImageOps.exif_transpose(opened).convert("RGB")
    if max(image.size) > budget.max_long_edge:
        image.thumbnail((budget.max_long_edge, budget.max_long_edge), Image.Resampling.LANCZOS)
    quality = budget.jpeg_quality
    encoded = b""
    for _ in range(10):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        encoded = buffer.getvalue()
        if len(encoded) <= budget.max_image_bytes:
            break
        if quality > 58:
            quality -= 8
        else:
            next_size = (max(1, int(image.width * 0.82)), max(1, int(image.height * 0.82)))
            if next_size == image.size:
                break
            image = image.resize(next_size, Image.Resampling.LANCZOS)
    if len(encoded) > budget.max_image_bytes:
        raise ReportGenerationError(
            f"视觉附件超过单图预算：{path.name} ({len(encoded)} > {budget.max_image_bytes} bytes)"
        )
    return PreparedVisual(
        image_name=image_name,
        role=role,
        source_path=str(path),
        source_width=source_width,
        source_height=source_height,
        source_bytes=source_bytes,
        sent_width=image.width,
        sent_height=image.height,
        sent_bytes=len(encoded),
        resized=(image.size != (source_width, source_height) or len(encoded) != source_bytes),
        data_url="data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii"),
    )


def prepare_visual_evidence(
    request: DamageReportRequest,
    *,
    image_names: Iterable[str] | None = None,
    budget: VisualBudget | None = None,
) -> list[PreparedVisual]:
    resolved_budget = budget or VisualBudget()
    referenced = set(image_names or (finding.image_name for finding in request.findings))
    sources: list[tuple[str, str, str]] = []
    for evidence in request.visual_evidence:
        if evidence.image_name not in referenced:
            continue
        sources.append((evidence.image_name, "original_image", evidence.image_path))
        if evidence.overlay_path:
            sources.append((evidence.image_name, "recognition_overlay", evidence.overlay_path))
    if referenced and not sources:
        raise ReportGenerationError("报告损伤项缺少可用原图或识别覆盖图")
    if len(sources) > resolved_budget.max_attachments:
        raise ReportGenerationError(
            f"视觉附件数量超过预算：{len(sources)} > {resolved_budget.max_attachments}"
        )
    prepared = [
        _encode_visual(
            image_name=image_name,
            role=role,
            path_value=path,
            budget=resolved_budget,
        )
        for image_name, role, path in sources
    ]
    total = sum(item.sent_bytes for item in prepared)
    if total > resolved_budget.max_total_bytes:
        raise ReportGenerationError(
            f"视觉附件总量超过预算：{total} > {resolved_budget.max_total_bytes} bytes"
        )
    return prepared


class ResponsesReportService:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        retries: int = 2,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_text_update: Callable[[str], None] | None = None,
        on_status_update: Callable[[dict[str, Any]], None] | None = None,
        visual_budget: VisualBudget | None = None,
        knowledge_base_tool: Callable[[str, int], dict[str, Any]] | None = None,
        ai_tool_rag: bool = False,
        max_knowledge_base_tool_calls: int = 3,
    ) -> None:
        self.api_key = api_key or os.getenv("DAMAGE_REPORT_API_KEY")
        self.base_url = normalize_responses_base_url(
            base_url or os.getenv("DAMAGE_REPORT_BASE_URL", DEFAULT_BASE_URL)
        )
        self.model = model or os.getenv("DAMAGE_REPORT_MODEL", DEFAULT_MODEL)
        self.timeout = float(timeout)
        self.retries = int(retries)
        self._sleep = sleep
        self._on_text_update = on_text_update
        self._on_status_update = on_status_update
        self.visual_budget = visual_budget or VisualBudget()
        self._knowledge_base_tool = knowledge_base_tool
        self._ai_tool_rag = bool(ai_tool_rag and knowledge_base_tool is not None)
        self._max_knowledge_base_tool_calls = max(1, int(max_knowledge_base_tool_calls))
        self.last_visual_audit: list[dict[str, Any]] = []
        self._is_compatible_endpoint = urlparse(self.base_url).hostname not in {
            "api.openai.com",
        }
        # Relay gateways commonly enforce a short non-streaming proxy timeout.
        # Start compatible endpoints on Chat Completions streaming so long
        # structured outputs keep the connection active while being generated.
        self._prefer_chat_completions = self._is_compatible_endpoint
        if self.timeout <= 0 or self.retries < 0:
            raise ReportConfigurationError("timeout must be positive and retries non-negative")
        if not self.api_key:
            raise ReportConfigurationError(
                "DAMAGE_REPORT_API_KEY is required; configure an application API key"
            )
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ReportConfigurationError(
                    "install the openai package in the active Python environment"
                ) from exc
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,
            )
        self.client = client

    def _emit_status(self, event: str, **details: Any) -> None:
        if self._on_status_update is None:
            return
        try:
            self._on_status_update({"event": event, **details})
        except Exception:
            pass

    @staticmethod
    def report_json_schema() -> dict[str, Any]:
        schema = DamageReportDraft.model_json_schema()
        findings = schema.get("properties", {}).get("findings")
        if isinstance(findings, dict):
            findings["minItems"] = 1
            findings["maxItems"] = 1
        schema["$schema"] = "http://json-schema.org/draft-07/schema#"
        return _make_schema_strict(schema)

    def _request(
        self,
        request: DamageReportRequest,
        *,
        generation_context: GenerationContext | None = None,
    ) -> DamageReport:
        payload_data: dict[str, Any] = request.prompt_payload()
        if generation_context is not None:
            payload_data = {
                "evidence": payload_data,
                "knowledge_base": generation_context.retrieved_chunks,
                "profile": generation_context.profile_name,
                "settings_snapshot_id": generation_context.settings_snapshot_id,
            }
        instructions = (
            "\n\n".join([REPORT_INSTRUCTIONS, generation_context.prompt])
            if generation_context is not None
            else REPORT_INSTRUCTIONS
        )
        prepared = prepare_visual_evidence(request, budget=self.visual_budget)
        self.last_visual_audit = [item.audit() for item in prepared]
        output_text = self.request_structured_output(
            payload_data=payload_data,
            instructions=instructions,
            schema_name="damage_report",
            schema=self.report_json_schema(),
            operation_name="vision report",
            image_data_urls=[item.data_url for item in prepared],
            max_output_tokens=6000,
        )
        draft = DamageReportDraft.model_validate(
            simplify_chinese_value(json.loads(output_text))
        )
        report = report_from_draft(draft, request=request, model=self.model)
        return simplify_chinese_model(report, DamageReport)

    def request_structured_output(
        self,
        *,
        payload_data: dict[str, Any],
        instructions: str,
        schema_name: str,
        schema: dict[str, Any],
        operation_name: str,
        image_data_urls: Iterable[str] = (),
        max_output_tokens: int | None = None,
        on_text_update: Callable[[str], None] | None = None,
    ) -> str:
        if on_text_update is None:
            on_text_update = self._on_text_update
        payload = json.dumps(payload_data, ensure_ascii=False, separators=(",", ":"))
        urls = list(image_data_urls)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._emit_status(
                "attempt_started",
                attempt=attempt + 1,
                max_attempts=self.retries + 1,
            )
            try:
                if self._ai_tool_rag:
                    self._emit_status("route_selected", route="ai_tool_rag")
                    output_text = self._request_tool_rag(
                        payload,
                        instructions=instructions,
                        schema_name=schema_name,
                        schema=schema,
                        image_data_urls=urls,
                        max_output_tokens=max_output_tokens,
                    )
                    self._emit_status("request_completed", attempt=attempt + 1)
                    return output_text
                if self._prefer_chat_completions:
                    self._emit_status("route_selected", route="chat_completions")
                    output_text = self._request_chat_fallback(
                        payload,
                        instructions=instructions,
                        schema_name=schema_name,
                        schema=schema,
                        image_data_urls=urls,
                        max_output_tokens=max_output_tokens,
                        on_text_update=on_text_update,
                    )
                else:
                    try:
                        self._emit_status("route_selected", route="responses")
                        content: str | list[dict[str, Any]] = payload
                        if urls:
                            content = [
                                {"type": "input_text", "text": payload},
                                *[
                                    {"type": "input_image", "image_url": image_url}
                                    for image_url in urls
                                ],
                            ]
                        request_kwargs = {
                            "model": self.model,
                            "instructions": instructions,
                            "input": ([{"role": "user", "content": content}] if isinstance(content, list) else content),
                            "stream": True,
                            "text": {
                                "format": {
                                    "type": "json_schema",
                                    "name": schema_name,
                                    "strict": True,
                                    "schema": schema,
                                }
                            },
                        }
                        if max_output_tokens is not None:
                            request_kwargs["max_output_tokens"] = int(max_output_tokens)
                        response = self.client.responses.create(**request_kwargs)
                    except Exception as exc:
                        if not self._is_compatible_endpoint or not self._is_transient(exc):
                            raise
                        self._prefer_chat_completions = True
                        self._emit_status("route_selected", route="chat_completions")
                        output_text = self._request_chat_fallback(
                            payload,
                            instructions=instructions,
                            schema_name=schema_name,
                            schema=schema,
                            image_data_urls=urls,
                            max_output_tokens=max_output_tokens,
                            on_text_update=on_text_update,
                        )
                        self._emit_status("request_completed", attempt=attempt + 1)
                        return output_text
                    output_text = extract_responses_stream_output_text(
                        response,
                        on_text_update=on_text_update,
                    )
                    if not output_text:
                        self._prefer_chat_completions = True
                        self._emit_status("route_selected", route="chat_completions")
                        output_text = self._request_chat_fallback(
                            payload,
                            instructions=instructions,
                            schema_name=schema_name,
                            schema=schema,
                            image_data_urls=urls,
                            max_output_tokens=max_output_tokens,
                            on_text_update=on_text_update,
                        )
                self._emit_status("request_completed", attempt=attempt + 1)
                return output_text
            except Exception as exc:
                last_error = exc
                if not self._is_transient(exc) or attempt >= self.retries:
                    status_code = getattr(exc, "status_code", None)
                    status_text = f"HTTP {status_code}, " if isinstance(status_code, int) else ""
                    cause_text = self._failure_cause_summary(exc)
                    raise ReportGenerationError(
                        f"Responses API {operation_name} generation failed: {type(exc).__name__} "
                        f"({status_text}{attempt + 1} attempts; cause: {cause_text})"
                    ) from exc
                retry_delay = (2**attempt) + random.random() * 0.25
                self._emit_status(
                    "retry_scheduled",
                    next_attempt=attempt + 2,
                    max_attempts=self.retries + 1,
                )
                self._sleep(retry_delay)
        raise ReportGenerationError(
            f"Responses API {operation_name} generation failed"
        ) from last_error

    @staticmethod
    def _knowledge_base_tool_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "name": "search_knowledge_base",
            "description": "在当前生成档案允许的知识库范围内检索与任务相关的工程资料。返回的片段只能用于参考，必须保留 source_marker。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要检索的工程问题或关键词"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        }

    def _request_tool_rag(
        self,
        payload: str,
        *,
        instructions: str,
        schema_name: str,
        schema: dict[str, Any],
        image_data_urls: Iterable[str] = (),
        max_output_tokens: int | None = None,
    ) -> str:
        """Run a bounded Responses/Chat tool loop selected by the model."""
        instructions = instructions + "\n\n如需工程规范、工法或材料依据，先调用 search_knowledge_base；不要假设已经提供了知识库片段。只能引用工具返回的 source_marker。"
        if self._prefer_chat_completions:
            return self._request_chat_tool_rag(
                payload, instructions=instructions, schema_name=schema_name,
                schema=schema, image_data_urls=image_data_urls,
                max_output_tokens=max_output_tokens,
            )
        urls = list(image_data_urls)
        content: str | list[dict[str, Any]] = payload
        if urls:
            content = [
                {"type": "input_text", "text": payload},
                *[{"type": "input_image", "image_url": url} for url in urls],
            ]
        input_items: list[Any] = [{"role": "user", "content": content}]
        tool_calls = 0
        for _ in range(self._max_knowledge_base_tool_calls + 1):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "instructions": instructions,
                "input": input_items,
                "tools": [self._knowledge_base_tool_schema()],
                "tool_choice": "auto",
                "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
            }
            if max_output_tokens is not None:
                kwargs["max_output_tokens"] = int(max_output_tokens)
            response = self.client.responses.create(**kwargs)
            output = getattr(response, "output", None)
            if output is None and isinstance(response, dict):
                output = response.get("output")
            output = list(output or [])
            calls = [item for item in output if self._field(item, "type") == "function_call"]
            if not calls:
                text = extract_responses_stream_output_text(response)
                if not text:
                    text = str(getattr(response, "output_text", "") or "")
                if text.strip():
                    return text
                raise ReportGenerationError("AI tool RAG returned no structured output")
            input_items.extend(output)
            for call in calls:
                if tool_calls >= self._max_knowledge_base_tool_calls:
                    raise ReportGenerationError("AI 知识库检索次数超过上限")
                tool_calls += 1
                name = str(self._field(call, "name") or "")
                if name != "search_knowledge_base":
                    result = {"error": "unsupported tool"}
                else:
                    try:
                        args = json.loads(str(self._field(call, "arguments") or "{}"))
                        result = self._knowledge_base_tool(str(args.get("query") or ""), int(args.get("top_k") or 6))  # type: ignore[misc]
                    except Exception as exc:
                        result = {"error": f"knowledge base search failed: {type(exc).__name__}: {exc}"}
                call_id = str(self._field(call, "call_id") or "")
                self._emit_status("knowledge_base_tool", call_id=call_id, result_count=len(result.get("chunks", [])) if isinstance(result, dict) else 0)
                input_items.append({"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)})
        raise ReportGenerationError("AI 知识库检索循环未产生最终结构化输出")

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    def _request_chat_tool_rag(self, payload: str, *, instructions: str, schema_name: str, schema: dict[str, Any], image_data_urls: Iterable[str] = (), max_output_tokens: int | None = None) -> str:
        chat = getattr(getattr(self.client, "chat", None), "completions", None)
        if chat is None or not hasattr(chat, "create"):
            raise ReportGenerationError("Chat Completions tool calling is unavailable")
        user_content: Any = payload
        urls = list(image_data_urls)
        if urls:
            user_content = [{"type": "text", "text": payload}, *[{"type": "image_url", "image_url": {"url": u}} for u in urls]]
        messages: list[dict[str, Any]] = [{"role": "system", "content": instructions}, {"role": "user", "content": user_content}]
        tool_calls = 0
        for _ in range(self._max_knowledge_base_tool_calls + 1):
            kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "tools": [{"type": "function", "function": {"name": "search_knowledge_base", "description": self._knowledge_base_tool_schema()["description"], "parameters": self._knowledge_base_tool_schema()["parameters"]}}], "tool_choice": "auto", "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}}}
            if max_output_tokens is not None:
                kwargs["max_tokens"] = int(max_output_tokens)
            response = chat.create(**kwargs)
            message = self._field((self._field(response, "choices", []) or [{}])[0], "message", {})
            calls = self._field(message, "tool_calls", []) or []
            if not calls:
                text = self._field(message, "content", "")
                if isinstance(text, str) and text.strip():
                    return text
                raise ReportGenerationError("AI tool RAG returned no structured output")
            messages.append({"role": "assistant", "content": self._field(message, "content", None), "tool_calls": calls})
            for call in calls:
                if tool_calls >= self._max_knowledge_base_tool_calls:
                    raise ReportGenerationError("AI 知识库检索次数超过上限")
                tool_calls += 1
                function = self._field(call, "function", {})
                try:
                    args = json.loads(str(self._field(function, "arguments") or "{}"))
                    result = self._knowledge_base_tool(str(args.get("query") or ""), int(args.get("top_k") or 6))  # type: ignore[misc]
                except Exception as exc:
                    result = {"error": f"knowledge base search failed: {type(exc).__name__}: {exc}"}
                messages.append({"role": "tool", "tool_call_id": str(self._field(call, "id") or ""), "content": json.dumps(result, ensure_ascii=False)})
        raise ReportGenerationError("AI 知识库检索循环未产生最终结构化输出")

    def _request_chat_fallback(
        self,
        payload: str,
        *,
        instructions: str,
        schema_name: str,
        schema: dict[str, Any],
        image_data_urls: Iterable[str] = (),
        max_output_tokens: int | None = None,
        on_text_update: Callable[[str], None] | None = None,
    ) -> str:
        chat = getattr(self.client, "chat", None)
        completions = getattr(chat, "completions", None)
        if completions is None or not hasattr(completions, "create"):
            raise ReportGenerationError(
                "Responses API returned no generated output text and Chat Completions is unavailable"
            )
        urls = list(image_data_urls)
        user_content: str | list[dict[str, Any]] = payload
        if urls:
            user_content = [
                {"type": "text", "text": payload},
                *[
                    {"type": "image_url", "image_url": {"url": image_url}}
                    for image_url in urls
                ],
            ]
        request_kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "stream": True,
        }
        if max_output_tokens is not None:
            request_kwargs["max_tokens"] = int(max_output_tokens)
        response = completions.create(**request_kwargs)
        # Test doubles and older gateways may ignore stream=True and return a
        # regular completion object; accept both representations.
        if not hasattr(response, "__iter__") or hasattr(response, "choices"):
            content = self._extract_chat_message_content(response)
            if on_text_update is not None:
                try:
                    on_text_update(content)
                except Exception:
                    pass
            return content
        parts: list[str] = []
        for chunk in response:
            choices = _response_field(chunk, "choices", [])
            if not isinstance(choices, (list, tuple)) or not choices:
                continue
            delta = _response_field(choices[0], "delta")
            content = _response_field(delta, "content")
            if isinstance(content, str):
                parts.append(content)
                if on_text_update is not None:
                    try:
                        on_text_update("".join(parts))
                    except Exception:
                        pass
        content = "".join(parts)
        if not content.strip():
            raise ReportGenerationError("Chat Completions streaming fallback returned no message content")
        return content

    @staticmethod
    def _extract_chat_message_content(response: Any) -> str:
        choices = _response_field(response, "choices", [])
        if not isinstance(choices, (list, tuple)) or not choices:
            raise ReportGenerationError("Chat Completions fallback returned no choices")
        message = _response_field(choices[0], "message")
        content = _response_field(message, "content")
        if not isinstance(content, str) or not content.strip():
            raise ReportGenerationError("Chat Completions fallback returned no message content")
        return content

    @staticmethod
    def _exception_chain(error: Exception) -> list[BaseException]:
        chain: list[BaseException] = []
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chain.append(current)
            current = current.__cause__ or current.__context__
        return chain

    @classmethod
    def _is_transient(cls, error: Exception) -> bool:
        sdk_connection_errors = {
            "APIConnectionError",
            "APITimeoutError",
            "RemoteProtocolError",
        }
        for item in cls._exception_chain(error):
            status_code = getattr(item, "status_code", None)
            if isinstance(status_code, int) and (status_code >= 500 or status_code == 429):
                return True
            if type(item).__name__ in sdk_connection_errors:
                return True
            if isinstance(item, (TimeoutError, ConnectionError, OSError)):
                return True
        return False

    def _failure_cause_summary(self, error: Exception) -> str:
        cause = self._exception_chain(error)[-1]
        detail = re.sub(r"\s+", " ", str(cause)).strip() or "no detail"
        if self.api_key:
            detail = detail.replace(self.api_key, "<redacted>")
        return f"{type(cause).__name__}: {detail[:240]}"

    def generate_report(
        self,
        request: DamageReportRequest,
        *,
        generation_context: GenerationContext | None = None,
    ) -> DamageReport:
        if len(request.findings) > 1:
            report = self._generate_chunked_report(request, generation_context=generation_context)
        else:
            report = self._request(request, generation_context=generation_context)
        return report

    def _generate_chunked_report(
        self,
        request: DamageReportRequest,
        *,
        generation_context: GenerationContext | None = None,
    ) -> DamageReport:
        findings = list(request.findings)
        batches = [[finding] for finding in findings]
        completed_text = ""

        def generate_batch(batch: list[Any]) -> tuple[list[ReportFinding], list[dict[str, Any]], str]:
            image_names = {finding.image_name for finding in batch}
            payload_data: dict[str, Any] = {
                "subject": request.subject.model_dump(mode="json"),
                "project_overview": request.subject.project_overview,
                "detection_hints": [
                    {
                        "finding_index": finding.index,
                        "image_name": finding.image_name,
                        "detected_damage_type": finding.class_name,
                        "detection_confidence": finding.detection_confidence,
                    }
                    for finding in batch
                ],
                "model_provenance": request.model_provenance,
                "inference_provenance": request.inference_provenance,
            }
            if generation_context is not None:
                payload_data = {
                    "evidence": payload_data,
                    "knowledge_base": generation_context.retrieved_chunks,
                    "profile": generation_context.profile_name,
                    "settings_snapshot_id": generation_context.settings_snapshot_id,
                }
                instructions = "\n\n".join([REPORT_INSTRUCTIONS, generation_context.profile_prompt])
            else:
                instructions = REPORT_INSTRUCTIONS
            try:
                prepared = prepare_visual_evidence(
                    request,
                    image_names=image_names,
                    budget=self.visual_budget,
                )
                def cumulative_update(text: str, prefix: str = completed_text) -> None:
                    if self._on_text_update is not None:
                        self._on_text_update(prefix + text)

                output_text = self.request_structured_output(
                    payload_data=payload_data,
                    instructions=instructions,
                    schema_name="damage_report_findings",
                    schema=_report_findings_json_schema(),
                    operation_name="vision report findings",
                    image_data_urls=[item.data_url for item in prepared],
                    max_output_tokens=6000,
                    on_text_update=(
                        cumulative_update if self._on_text_update is not None else None
                    ),
                )
                parsed = json.loads(output_text)
                if not isinstance(parsed, dict):
                    raise ValueError("report findings response must be an object")
                generated = [
                    ReportFinding.model_validate(simplify_chinese_value(item))
                    for item in parsed.get("findings", [])
                ]
                expected = [(finding.image_name, finding.index) for finding in batch]
                ordered, _ = validate_and_order_findings(expected, generated)
                return ordered, [item.audit() for item in prepared], output_text
            except (ReportGenerationError, ValueError, TypeError, ValidationError, json.JSONDecodeError) as exc:
                raise ReportGenerationError(str(exc)) from exc

        generated_findings: list[ReportFinding] = []
        visual_audit: list[dict[str, Any]] = []
        for batch in batches:
            generated, audit, raw = generate_batch(batch)
            generated_findings.extend(generated)
            visual_audit.extend(audit)
            completed_text += raw + "\n"

        generated_findings, integrity = validate_and_order_findings(
            request.identities(), generated_findings
        )
        self.last_visual_audit = visual_audit
        overall = max(
            (item.damage_level for item in generated_findings),
            key=lambda level: SEVERITY_ORDER[level],
            default="undetermined",
        )
        executive_summary, overall_level_reason = _build_multi_finding_summary(
            generated_findings
        )

        report = DamageReport(
            report_schema_version=REPORT_SCHEMA_VERSION,
            subject=request.subject,
            executive_summary=executive_summary,
            overall_screening_level=overall,
            overall_level_reason=overall_level_reason,
            findings=generated_findings,
            limitations=["未使用像素尺寸、物理尺寸、面积比例或数值阈值进行损伤评定。"],
            review_status="pending_human_review",
            human_review=HumanReview(),
            integrity=integrity,
            provenance=new_report_provenance(
                model=self.model,
                source_summary_path=request.source_summary_path,
                evidence_count=len(generated_findings),
            ),
        )
        return simplify_chinese_model(report, DamageReport)

    def generate_report_from_summary(
        self,
        summary_path: str | Path,
        *,
        output_dir: str | Path | None = None,
        generation_context: GenerationContext | None = None,
    ) -> DamageReport:
        source_path = Path(summary_path)
        summary = json.loads(source_path.read_text(encoding="utf-8"))
        request = DamageReportRequest.from_summary(summary, str(source_path))
        report = self.generate_report(request, generation_context=generation_context)
        _apply_project_overview_to_report(report, request.subject.project_overview)
        target_dir = Path(output_dir) if output_dir is not None else source_path.parent
        _persist_report(
            report,
            target_dir=target_dir,
            request=request,
            generation_context=generation_context,
            fallback=False,
            fallback_reason="",
            visual_audit=self.last_visual_audit,
        )
        return report


def _persist_report(
    report: DamageReport,
    *,
    target_dir: Path,
    request: DamageReportRequest | None,
    generation_context: GenerationContext | None,
    fallback: bool,
    fallback_reason: str,
    visual_audit: list[dict[str, Any]] | None = None,
    existing_payload: dict[str, Any] | None = None,
) -> None:
    report = simplify_chinese_model(report, DamageReport)
    target_dir.mkdir(parents=True, exist_ok=True)
    mode = "local_no_vision_fallback" if fallback else "remote_vision_ai"
    audit = _generation_audit(
        mode=mode,
        model=report.provenance.model,
        generation_context=generation_context,
        fallback_reason=fallback_reason,
    )
    audit["visual_attachments"] = list(visual_audit or [])
    payload = dict(existing_payload or {})
    changes = (
        _ai_output_changes(request, report)
        if request is not None
        else payload.get("ai_output_changes", {})
    )
    citation_catalog = (
        build_citation_catalog(generation_context.retrieved_chunks)
        if generation_context is not None
        else normalize_citation_catalog(payload.get("citation_catalog"))
    )
    payload.update(
        {
            "report": report.model_dump(mode="json"),
            "evidence_snapshot": request.prompt_payload() if request is not None else payload.get("evidence_snapshot", {}),
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "fallback": fallback,
            "fallback_reason": redact_secret_text(fallback_reason) if fallback_reason else "",
            "ai_output_changes": changes,
            "generation_audit": audit if request is not None else payload.get("generation_audit", audit),
            "citation_catalog": citation_catalog,
        }
    )
    report_json = target_dir / "report.json"
    report_md = target_dir / "report.md"
    atomic_write_json(report_json, payload)
    atomic_write_text(
        report_md,
        render_report_markdown(
            report,
            generation_audit=payload["generation_audit"],
            citation_catalog=citation_catalog,
        ),
    )
    if generation_context is not None:
        profile_manifest = generation_context.manifest(model=report.provenance.model)
        profile_manifest["ai_output_changes"] = changes
        profile_manifest["visual_attachments"] = list(visual_audit or [])
        merge_profile_manifest(
            target_dir / "generation_manifest.json",
            profile_name=generation_context.profile_name,
            profile_manifest=profile_manifest,
            outputs={"json": str(report_json), "markdown": str(report_md)},
            fallback=fallback,
            fallback_reason=fallback_reason,
        )


def _expected_identities_from_snapshot(snapshot: dict[str, Any]) -> list[tuple[str, int]]:
    hints = snapshot.get("detection_hints", []) if isinstance(snapshot, dict) else []
    return [
        (str(item.get("image_name", "")), int(item.get("finding_index", 0)))
        for item in hints
        if isinstance(item, dict)
    ]


def save_human_reviewed_report(
    report_path: str | Path,
    report: DamageReport,
    *,
    confirmed: bool,
) -> DamageReport:
    path = Path(report_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    expected = _expected_identities_from_snapshot(dict(persisted.get("evidence_snapshot") or {}))
    if confirmed:
        validate_report_for_confirmation(report, expected_identities=expected)
        report.review_status = "confirmed_by_human"
        report.human_review.status = "confirmed_by_human"
        report.human_review.reviewed_at = datetime.now(timezone.utc).isoformat()
    else:
        ordered, integrity = validate_and_order_findings(expected, report.findings)
        report.findings = ordered
        report.integrity = integrity
        report.review_status = "edited_pending_confirmation"
        report.human_review.status = "edited_pending_confirmation"
        report.human_review.reviewed_at = None
    report.report_schema_version = REPORT_SCHEMA_VERSION
    report.provenance.report_schema_version = REPORT_SCHEMA_VERSION
    _persist_report(
        report,
        target_dir=path.parent,
        request=None,
        generation_context=None,
        fallback=bool(persisted.get("fallback", False)),
        fallback_reason=str(persisted.get("fallback_reason", "") or ""),
        existing_payload=persisted,
    )
    return report


def load_confirmed_report(report_path: str | Path) -> DamageReport:
    """Load and independently revalidate the persisted human-confirmed v3 report."""
    path = Path(report_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    payload = persisted.get("report", persisted)
    report = simplify_chinese_model(DamageReport.model_validate(payload), DamageReport)
    if report.report_schema_version != REPORT_SCHEMA_VERSION:
        raise ValueError("旧版报告不能用于下游方案，请重新生成 damage-report.v3")
    if report.review_status != "confirmed_by_human":
        raise ValueError("损伤报告尚未人工确认，不能生成下游方案")
    if report.human_review.status != "confirmed_by_human" or not report.human_review.reviewed_at:
        raise ValueError("损伤报告缺少有效人工确认记录")
    expected = _expected_identities_from_snapshot(dict(persisted.get("evidence_snapshot") or {}))
    return validate_report_for_confirmation(report, expected_identities=expected)


def render_report_markdown(
    report: DamageReport,
    *,
    generation_audit: dict[str, Any] | None = None,
    citation_catalog: Iterable[dict[str, Any]] | None = None,
) -> str:
    report = simplify_chinese_model(report, DamageReport)
    audit = generation_audit or _generation_audit(
        mode=("local_fallback" if report.provenance.model == "local-evidence-fallback" else "remote_ai"),
        model=report.provenance.model,
        generation_context=None,
    )
    mode_label = "远程视觉 AI" if audit["generation_mode"] == "remote_vision_ai" else "本地不判级降级"
    knowledge_label = "已使用" if audit["knowledge_base_used"] else "未使用"
    cite = lambda value: render_knowledge_citations(value, citation_catalog)
    lines = [
        "# 损伤分析报告",
        "",
        f"**报告版本**：`{report.report_schema_version}`  ",
        f"**AI 辅助等级**：{report.overall_screening_level}  ",
        f"**人工复核状态**：{report.review_status}  ",
        f"**生成方式**：{mode_label}  ",
        f"**生成模型**：{audit['model']}  ",
        f"**知识库增强**：{knowledge_label}",
        "",
    ]
    if report.subject.project_overview:
        lines.extend(["## 项目概括", "", cite(report.subject.project_overview), ""])
    lines.extend([
        "## AI 辅助判断", "", cite(report.executive_summary), "",
        f"**总体等级理由**：{cite(report.overall_level_reason or '未提供')}", "", "## 损伤明细", "",
    ])
    for finding in report.findings:
        lines.extend(
            [
                f"### {finding.image_name} · #{finding.finding_index} · {finding.damage_type}",
                f"- AI 辅助等级：{finding.damage_level}",
                f"- 等级理由：{cite(finding.level_reason or '未提供')}",
                f"- 可见依据：{cite('；'.join(finding.visual_basis) or finding.observed_evidence)}",
                f"- 风险解释：{cite(finding.risk_interpretation)}",
                f"- 不确定性：{cite(finding.uncertainty or finding.confidence_note)}",
                f"- 建议：{cite(finding.recommended_action)}",
            ]
        )
        if finding.standards_basis:
            lines.append("- 参考规范：")
            for reference in finding.standards_basis:
                lines.append(
                    f"  - {cite(reference.name)}（{cite(reference.id)}）：{cite(reference.role)}"
                )
        lines.append("")
    lines.extend([
        "## 人工复核", "",
        f"- 状态：{report.human_review.status}",
        f"- 复核人：{report.human_review.reviewer or '未填写'}",
        f"- 复核时间：{report.human_review.reviewed_at or '未确认'}",
        f"- 复核备注：{cite(report.human_review.notes or '无')}", "",
        "## 局限性", "", *[f"- {cite(item)}" for item in report.limitations], "",
    ])
    return "\n".join(lines)


def _apply_project_overview_to_report(report: DamageReport, overview: str | None) -> None:
    exact_overview = str(overview or "")
    report.subject.project_overview = exact_overview if exact_overview.strip() else None
    controls = analyze_project_overview(exact_overview)
    if controls.report_notes:
        summary_note = "项目背景关注：" + "；".join(controls.report_notes) + "。"
        if summary_note not in report.executive_summary:
            report.executive_summary = f"{report.executive_summary.rstrip()} {summary_note}"
    for note in controls.report_notes:
        if note not in report.limitations:
            report.limitations.append(note)
    for limitation in controls.limitations:
        if limitation not in report.limitations:
            report.limitations.append(limitation)


def build_local_fallback_report_from_summary(
    summary_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    reason: str,
    generation_context: GenerationContext | None = None,
) -> DamageReport:
    reason = redact_secret_text(reason)
    source_path = Path(summary_path)
    summary = json.loads(source_path.read_text(encoding="utf-8"))
    request = DamageReportRequest.from_summary(summary, str(source_path))
    findings = [
        ReportFinding(
            finding_index=finding.index,
            image_name=finding.image_name,
            damage_type=finding.class_name,
            damage_level="undetermined",
            level_reason="视觉模型服务不可用，系统未进行损伤分级。",
            standards_basis=[],
            visual_basis=["本地识别模型提示存在该损伤类型"],
            uncertainty="未完成在线视觉模型复核，不能形成辅助等级。",
            observed_evidence=(
                f"本地模型识别类型：{finding.class_name}；"
                f"识别置信度 {finding.detection_confidence:.1%}。"
            ),
            risk_interpretation="仅保留识别线索，不作损伤等级或结构安全判断。",
            recommended_action="恢复视觉模型服务后重新生成，并由专业人员复核。",
            confidence_note="识别置信度不等同于损伤等级或结构安全结论。",
        )
        for finding in request.findings
    ]
    report = DamageReport(
        report_schema_version=REPORT_SCHEMA_VERSION,
        subject=request.subject,
        executive_summary=(
            ("本报告结合用户填写的项目概括进行项目背景说明。" if request.subject.project_overview else "")
            +
            f"在线视觉模型服务不可用；系统仅保留 {len(findings)} 项识别线索，未进行损伤分级。"
        ),
        overall_screening_level="undetermined",
        overall_level_reason="缺少在线视觉模型的图像判断，不能给出辅助等级。",
        findings=findings,
        limitations=[
            f"远程报告服务失败：{reason}",
            "未使用像素尺寸、物理尺寸、面积比例或数值阈值进行损伤评定。",
            "恢复在线视觉模型服务后应重新生成，并由专业人员确认。",
        ],
        review_status="pending_human_review",
        human_review=HumanReview(),
        integrity=ReportIntegrity(
            correspondence_valid=True,
            expected_count=len(findings),
            actual_count=len(findings),
        ),
        provenance=new_report_provenance(
            model="local-evidence-fallback",
            source_summary_path=str(source_path),
            evidence_count=len(findings),
        ),
    )
    _apply_project_overview_to_report(report, request.subject.project_overview)
    target_dir = Path(output_dir) if output_dir is not None else source_path.parent
    _persist_report(
        report,
        target_dir=target_dir,
        request=request,
        generation_context=generation_context,
        fallback=True,
        fallback_reason=reason,
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a structured damage report from summary.json")
    parser.add_argument("summary", type=Path)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--retries", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = ResponsesReportService(
        base_url=args.base_url,
        model=args.model,
        timeout=args.timeout,
        retries=args.retries,
    )
    service.generate_report_from_summary(args.summary, output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
