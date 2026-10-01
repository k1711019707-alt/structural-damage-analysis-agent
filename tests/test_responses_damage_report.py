from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _finding() -> dict[str, object]:
    return {
        "index": 0,
        "image_name": "beam.jpg",
        "class_id": 6,
        "class_name": "Structural crack",
        "score": 0.94,
        "detection_confidence": 0.94,
        "box": [1.0, 2.0, 10.0, 12.0],
        "area": {
            "area_pixels": 12,
            "area_ratio": 0.03,
            "measurement_width_px": 20,
            "measurement_height_px": 20,
            "source_image_width_px": 20,
            "source_image_height_px": 20,
        },
        "crack_geometry": {
            "applicable": True,
            "length_px": 12.0,
            "mean_width_px": 1.0,
            "maximum_width_px": 2.0,
            "physical_length": None,
            "physical_width": None,
            "physical_unit": None,
            "calibration_available": False,
        },
        "screening_severity": {
            "level": "medium",
            "rule_id": "affected-image-area-ratio",
            "rule_version": "1.0.0",
            "observed_area_ratio": 0.03,
            "basis": "Deterministic screening based on affected image-area ratio.",
            "limitations": "Preliminary screening only.",
        },
    }


def _report_json() -> str:
    return json.dumps(
        {
            "subject": {
                "project_name": None,
                "asset_id": None,
                "component": None,
                "inspection_time": None,
                "project_overview": None,
            },
            "executive_summary": "检测到一处结构裂缝，建议工程师复核。",
            "overall_screening_level": "medium",
            "overall_level_reason": "视觉证据显示需要现场复核。",
            "findings": [
                {
                    "finding_index": 0,
                    "image_name": "beam.jpg",
                    "damage_type": "Structural crack",
                    "damage_level": "medium",
                    "level_reason": "原图可见连续裂缝。",
                    "standards_basis": [],
                    "visual_basis": ["原图和识别覆盖图定位一致"],
                    "uncertainty": "未提供可靠物理尺度。",
                    "observed_evidence": "原图可见连续裂缝。",
                    "risk_interpretation": "可能需要进一步复核。",
                    "recommended_action": "现场复核并补充标定。",
                    "confidence_note": "模型置信度为0.94。",
                }
            ],
            "limitations": ["未提供像素到物理长度的标定。"],
        },
        ensure_ascii=False,
    )


class _FakeResponses:
    def __init__(
        self,
        output_text: str | None = None,
        errors: list[Exception] | None = None,
        response_output: object | None = None,
    ):
        self.output_text = output_text
        self.errors = list(errors or [])
        self.response_output = response_output
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return SimpleNamespace(output_text=self.output_text, output=self.response_output)


class _StreamingFakeResponses(_FakeResponses):
    def __init__(self, events: list[object]):
        super().__init__()
        self.events = events

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.events)


class _FakeClient:
    def __init__(self, responses: _FakeResponses, chat_completions=None):
        self.responses = responses
        if chat_completions is not None:
            self.chat = SimpleNamespace(completions=chat_completions)


class _FakeChatCompletions:
    def __init__(self, content: str, errors: list[Exception] | None = None):
        self.content = content
        self.errors = list(errors or [])
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def _chat_payload(kwargs: dict[str, object]) -> dict[str, object]:
    content = kwargs["messages"][1]["content"]
    if isinstance(content, list):
        content = next(item["text"] for item in content if item.get("type") == "text")
    return json.loads(content)


def _responses_payload(call: dict[str, object]) -> dict[str, object]:
    value = call["input"]
    if isinstance(value, list):
        content = value[0]["content"]
        value = next(item["text"] for item in content if item.get("type") == "input_text")
    return json.loads(value)


def _chunk_finding(item: dict[str, object]) -> dict[str, object]:
    return {
        "finding_index": item["finding_index"],
        "image_name": item["image_name"],
        "damage_type": item["detected_damage_type"],
        "damage_level": "medium",
        "level_reason": "视觉证据显示需要现场复核。",
        "standards_basis": [],
        "visual_basis": ["原图和识别覆盖图"],
        "uncertainty": "未提供可靠物理尺度。",
        "observed_evidence": "仅依据输入视觉证据。",
        "risk_interpretation": "需工程师复核。",
        "recommended_action": "现场复核。",
        "confidence_note": "模型置信度不等于安全结论。",
    }


def _visual_evidence(tmp_path: Path, *image_names: str):
    from PIL import Image
    from runtime.damage_report_schema import VisualEvidence

    items = []
    for image_name in image_names:
        stem = Path(image_name).stem
        original = tmp_path / f"{stem}-original.png"
        overlay = tmp_path / f"{stem}-overlay.png"
        Image.new("RGB", (8, 8), (240, 240, 240)).save(original)
        Image.new("RGB", (8, 8), (255, 0, 0)).save(overlay)
        items.append(
            VisualEvidence(
                image_name=image_name,
                image_path=str(original),
                overlay_path=str(overlay),
                processing_status="success",
            )
        )
    return items


def _request(tmp_path: Path):
    from runtime.damage_report_schema import DamageReportRequest, ReportSubject

    return DamageReportRequest(
        subject=ReportSubject(
            project_name="test", asset_id="A1", component="beam", inspection_time=None
        ),
        findings=[
            __import__("runtime.damage_report_schema", fromlist=["DamageFinding"]).DamageFinding.model_validate(_finding())
        ],
        visual_evidence=_visual_evidence(tmp_path, "beam.jpg"),
        source_summary_path=str(tmp_path / "summary.json"),
        model_provenance={"model_filename": "best.pt"},
        inference_provenance={"severity_rule_version": "1.0.0"},
    )


def test_pydantic_report_request_rejects_missing_evidence() -> None:
    from runtime.damage_report_schema import DamageFinding

    broken = _finding()
    del broken["class_name"]
    with pytest.raises(Exception):
        DamageFinding.model_validate(broken)


def test_responses_service_sends_strict_schema_and_validates_output(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(_report_json())
    service = ResponsesReportService(
        api_key="test-key",
        model="test-model",
        client=_FakeClient(responses),
        sleep=lambda _seconds: None,
    )
    report = service.generate_report(_request(tmp_path))

    assert report.review_status == "pending_human_review"
    assert responses.calls[0]["model"] == "test-model"
    assert responses.calls[0]["text"]["format"]["type"] == "json_schema"
    assert responses.calls[0]["text"]["format"]["strict"] is True
    assert "visual inspection assistant" in responses.calls[0]["instructions"]


def test_remote_report_preserves_reordered_findings_and_ai_damage_classes(
    tmp_path: Path,
) -> None:
    from runtime.damage_report_schema import DamageFinding, DamageReportRequest, ReportSubject
    from runtime.responses_damage_report import ResponsesReportService

    crack = _finding()
    deformation = {**_finding(), "image_name": "column.jpg", "class_name": "Structural deformation"}
    request = DamageReportRequest(
        subject=ReportSubject(
            project_name="test", asset_id="A1", component="mixed", inspection_time=None
        ),
        findings=[
            DamageFinding.model_validate(crack),
            DamageFinding.model_validate(deformation),
        ],
        visual_evidence=_visual_evidence(tmp_path, "beam.jpg", "column.jpg"),
        source_summary_path=str(tmp_path / "summary.json"),
        model_provenance={},
        inference_provenance={},
    )
    payload = json.loads(_report_json())
    crack_report = dict(payload["findings"][0])
    crack_report["damage_type"] = "结构裂缝（Structural crack 的中文说明）"
    deformation_report = {
        **crack_report,
        "image_name": "column.jpg",
        "damage_type": "结构变形",
        "observed_evidence": "检测结果显示构件存在结构变形。",
    }
    class PerFindingResponses(_FakeResponses):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            request_payload = _responses_payload(kwargs)
            hint = request_payload["detection_hints"][0]
            finding = crack_report if hint["image_name"] == "beam.jpg" else deformation_report
            return SimpleNamespace(
                output_text=json.dumps({"findings": [finding]}, ensure_ascii=False),
                output=None,
            )

    responses = PerFindingResponses()
    service = ResponsesReportService(api_key="test-key", client=_FakeClient(responses))

    report = service.generate_report(request)

    assert [item.image_name for item in report.findings] == ["beam.jpg", "column.jpg"]
    assert [item.damage_type for item in report.findings] == [
        "结构裂缝（Structural crack 的中文说明）",
        "结构变形",
    ]


@pytest.mark.parametrize("identity_mode", ["duplicate", "missing"])
def test_remote_report_rejects_changed_finding_identity_set(
    tmp_path: Path,
    identity_mode: str,
) -> None:
    from runtime.damage_report_schema import DamageFinding, DamageReportRequest, ReportSubject
    from runtime.responses_damage_report import ResponsesReportService

    second = {**_finding(), "image_name": "column.jpg", "class_name": "Structural deformation"}
    request = DamageReportRequest(
        subject=ReportSubject(
            project_name="test", asset_id="A1", component="mixed", inspection_time=None
        ),
        findings=[
            DamageFinding.model_validate(_finding()),
            DamageFinding.model_validate(second),
        ],
        visual_evidence=_visual_evidence(tmp_path, "beam.jpg", "column.jpg"),
        source_summary_path=str(tmp_path / "summary.json"),
        model_provenance={},
        inference_provenance={},
    )
    payload = json.loads(_report_json())
    first_report = dict(payload["findings"][0])
    payload["findings"] = (
        [first_report, first_report]
        if identity_mode == "duplicate"
        else [first_report]
    )
    responses = _FakeResponses(json.dumps(payload, ensure_ascii=False))
    service = ResponsesReportService(api_key="test-key", client=_FakeClient(responses))

    with pytest.raises(Exception, match="correspondence mismatch"):
        service.generate_report(request)


def test_custom_report_profile_prompt_is_sent_without_business_restrictions(tmp_path: Path) -> None:
    from runtime.generation_context import GenerationContext
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(_report_json())
    context = GenerationContext(
        profile_name="損傷分析報告",
        profile_version=2,
        settings_snapshot_id="snapshot",
        evidence={},
        knowledge_base_used=True,
        retrieved_chunks=[{"source_marker": "[KB:report:1]", "text": "参考"}],
        prompt="请使用风险分级表述，并增加现场复核章节。",
        profile_prompt="请使用风险分级表述，并增加现场复核章节。",
    )
    service = ResponsesReportService(
        api_key="test-key",
        model="test-model",
        client=_FakeClient(responses),
        sleep=lambda _seconds: None,
    )
    service.generate_report(_request(tmp_path), generation_context=context)

    instructions = responses.calls[0]["instructions"]
    assert "增加现场复核章节" in instructions
    assert "immutable" not in instructions.casefold()
    assert "不得引用未提供" not in instructions


def test_custom_report_profile_prompt_is_sent_for_batched_requests(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageFinding, DamageReportRequest, ReportSubject
    from runtime.generation_context import GenerationContext
    from runtime.responses_damage_report import ResponsesReportService

    source = _finding()
    findings = []
    for index in range(9):
        item = dict(source)
        item["index"] = index
        findings.append(DamageFinding.model_validate(item))
    request = DamageReportRequest(
        subject=ReportSubject(project_name="test", asset_id=None, component=None, inspection_time=None),
        findings=findings,
        visual_evidence=_visual_evidence(tmp_path, "beam.jpg"),
        source_summary_path=str(tmp_path / "summary.json"),
        model_provenance={},
        inference_provenance={},
    )

    class ChunkChat:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            payload = _chat_payload(kwargs)
            evidence = payload.get("evidence", payload)
            generated = {"findings": [
                _chunk_finding(item)
                for item in evidence["detection_hints"]
            ]}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(generated, ensure_ascii=False)))])

    chat = ChunkChat()
    context = GenerationContext(
        profile_name="損傷分析報告", profile_version=2, settings_snapshot_id="snapshot", evidence={},
        knowledge_base_used=True, retrieved_chunks=[], prompt="批次输出应突出裂缝复核。", profile_prompt="批次输出应突出裂缝复核。",
    )
    service = ResponsesReportService(
        api_key="test-key", base_url="https://compatible.example/v1", client=_FakeClient(_FakeResponses(), chat)
    )
    service.generate_report(request, generation_context=context)
    assert len(chat.calls) == 9
    assert all(
        len(_chat_payload(call)["evidence"]["detection_hints"]) == 1
        for call in chat.calls
    )
    assert all("批次输出应突出裂缝复核" in call["messages"][0]["content"] for call in chat.calls)


def test_large_report_is_generated_in_chunks_and_reassembled(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageFinding, DamageReportRequest, ReportSubject
    from runtime.responses_damage_report import ResponsesReportService

    source = _finding()
    findings = []
    for index in range(9):
        item = dict(source)
        item["index"] = index
        findings.append(DamageFinding.model_validate(item))
    request = DamageReportRequest(
        subject=ReportSubject(project_name="test", asset_id=None, component=None, inspection_time=None),
        findings=findings,
        visual_evidence=_visual_evidence(tmp_path, "beam.jpg"),
        source_summary_path=str(tmp_path / "summary.json"),
        model_provenance={},
        inference_provenance={},
    )

    class ChunkChat:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            payload = _chat_payload(kwargs)
            evidence = payload.get("evidence", payload)
            generated = {
                "findings": [
                        _chunk_finding(item)
                        for item in evidence["detection_hints"]
                    ]
                }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(generated, ensure_ascii=False)))]
            )

    chat = ChunkChat()
    service = ResponsesReportService(
        api_key="test-key",
        base_url="https://compatible.example/v1",
        client=_FakeClient(_FakeResponses(), chat),
    )
    report = service.generate_report(request)

    assert len(chat.calls) == 9
    assert all(len(_chat_payload(call)["detection_hints"]) == 1 for call in chat.calls)
    assert len(report.findings) == 9
    assert [item.finding_index for item in report.findings] == list(range(9))
    assert "本次共分析9项损伤" in report.executive_summary
    assert "结构裂缝9项" in report.executive_summary
    assert "总体辅助等级为中等" in report.overall_level_reason
    assert "不等同于结构安全等级" in report.overall_level_reason
    assert "工程师审核" in report.overall_level_reason


def test_report_remote_schemas_hard_bound_each_finding_response() -> None:
    from runtime.responses_damage_report import (
        ResponsesReportService,
        _report_findings_json_schema,
    )

    for schema in (ResponsesReportService.report_json_schema(), _report_findings_json_schema()):
        findings = schema["properties"]["findings"]
        assert findings["minItems"] == findings["maxItems"] == 1
        serialized = json.dumps(schema)
        assert "maxLength" in serialized
        assert "maxItems" in serialized

    atomic_schema = _report_findings_json_schema()
    assert "StandardReference" in atomic_schema["$defs"]
    assert "$defs" not in atomic_schema["properties"]["findings"]["items"]


def test_responses_service_extracts_standard_message_output_when_sdk_text_is_missing(
    tmp_path: Path,
) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    response_output = [
        {
            "type": "message",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": _report_json()},
            ],
        }
    ]
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(_FakeResponses(response_output=response_output)),
    )

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary == "检测到一处结构裂缝，建议工程师复核。"


def test_responses_service_streams_and_aggregates_text_delta_events(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    payload = _report_json()
    responses = _StreamingFakeResponses(
        [
            SimpleNamespace(type="response.created"),
            SimpleNamespace(type="response.output_text.delta", delta=payload[:80]),
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="ignore"),
            {"type": "response.output_text.delta", "delta": payload[80:]},
            SimpleNamespace(type="response.completed"),
        ]
    )
    service = ResponsesReportService(api_key="test-key", client=_FakeClient(responses))

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary == "检测到一处结构裂缝，建议工程师复核。"
    assert responses.calls[0]["stream"] is True


def test_responses_service_accepts_complete_response_when_stream_is_ignored(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(_report_json())
    chat = _FakeChatCompletions(_report_json())
    service = ResponsesReportService(api_key="test-key", client=_FakeClient(responses, chat))

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary == "检测到一处结构裂缝，建议工程师复核。"
    assert responses.calls[0]["stream"] is True
    assert chat.calls == []


def test_responses_service_empty_stream_uses_existing_failure_path(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    responses = _StreamingFakeResponses([SimpleNamespace(type="response.completed")])
    chat = _FakeChatCompletions(_report_json())
    service = ResponsesReportService(api_key="test-key", client=_FakeClient(responses, chat))

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary
    assert responses.calls[0]["stream"] is True
    assert len(chat.calls) == 1


def test_responses_service_uses_chat_fallback_for_empty_completed_output(
    tmp_path: Path,
) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(response_output=[])
    chat = _FakeChatCompletions(_report_json())
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses, chat),
    )

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary
    assert len(responses.calls) == 1
    assert len(chat.calls) == 1
    assert chat.calls[0]["response_format"]["type"] == "json_schema"
    assert chat.calls[0]["response_format"]["json_schema"]["strict"] is True
    assert chat.calls[0]["stream"] is True


def test_compatible_endpoint_prefers_streaming_chat_for_long_outputs(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(_report_json())
    chat = _FakeChatCompletions(_report_json())
    service = ResponsesReportService(
        api_key="test-key",
        base_url="https://compatible.example/v1",
        client=_FakeClient(responses, chat),
    )

    service.generate_report(_request(tmp_path))

    assert responses.calls == []
    assert len(chat.calls) == 1
    assert chat.calls[0]["stream"] is True


def test_streaming_chat_content_is_aggregated(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    class StreamingChat:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            payload = _report_json()
            return iter(
                [
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=payload[:80]))]),
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=payload[80:]))]),
                ]
            )

    service = ResponsesReportService(
        api_key="test-key",
        base_url="https://compatible.example/v1",
        client=_FakeClient(_FakeResponses(), StreamingChat()),
    )

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary == "检测到一处结构裂缝，建议工程师复核。"


def test_responses_stream_publishes_accumulated_text_updates(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    payload = _report_json()
    responses = _StreamingFakeResponses(
        [
            SimpleNamespace(type="response.output_text.delta", delta=payload[:50]),
            SimpleNamespace(type="response.output_text.delta", delta=payload[50:]),
        ]
    )
    updates: list[str] = []
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses),
        on_text_update=updates.append,
    )

    service.generate_report(_request(tmp_path))

    assert updates == [payload[:50], payload]


def test_generation_update_callback_failure_does_not_fail_report(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    payload = _report_json()
    responses = _StreamingFakeResponses(
        [SimpleNamespace(type="response.output_text.delta", delta=payload)]
    )

    def broken_update(_text: str) -> None:
        raise RuntimeError("display is closed")

    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses),
        on_text_update=broken_update,
    )

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary == "检测到一处结构裂缝，建议工程师复核。"


def test_valid_responses_output_does_not_call_chat_fallback(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(_report_json())
    chat = _FakeChatCompletions(_report_json())
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses, chat),
    )

    service.generate_report(_request(tmp_path))

    assert len(responses.calls) == 1
    assert chat.calls == []


def test_retry_prefers_chat_after_endpoint_returns_empty_responses_output(
    tmp_path: Path,
) -> None:
    from openai import APIConnectionError
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(response_output=[])
    chat = _FakeChatCompletions(
        _report_json(),
        errors=[APIConnectionError(request=None)],
    )
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses, chat),
        retries=1,
        sleep=lambda _seconds: None,
    )

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary
    assert len(responses.calls) == 1
    assert len(chat.calls) == 2


def test_compatible_endpoint_uses_chat_after_responses_connection_error(
    tmp_path: Path,
) -> None:
    from openai import APIConnectionError
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(errors=[APIConnectionError(request=None)])
    chat = _FakeChatCompletions(_report_json())
    service = ResponsesReportService(
        api_key="test-key",
        base_url="https://compatible.example/v1",
        client=_FakeClient(responses, chat),
        retries=1,
        sleep=lambda _seconds: None,
    )

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary
    assert responses.calls == []
    assert len(chat.calls) == 1


def test_official_endpoint_retries_responses_connection_error_without_chat(
    tmp_path: Path,
) -> None:
    from openai import APIConnectionError
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(
        _report_json(),
        errors=[APIConnectionError(request=None)],
    )
    chat = _FakeChatCompletions(_report_json())
    service = ResponsesReportService(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        client=_FakeClient(responses, chat),
        retries=1,
        sleep=lambda _seconds: None,
    )

    service.generate_report(_request(tmp_path))

    assert len(responses.calls) == 2
    assert chat.calls == []


def test_responses_service_submits_all_object_properties_as_required(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(_report_json())
    service = ResponsesReportService(api_key="test-key", client=_FakeClient(responses))

    service.generate_report(_request(tmp_path))

    schema = responses.calls[0]["text"]["format"]["schema"]
    subject = schema["$defs"]["ReportSubject"]
    assert set(subject["required"]) == set(subject["properties"])
    assert "null" in str(subject["properties"]["project_overview"])


def test_responses_service_retries_transient_failures(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    transient = TimeoutError("temporary")
    responses = _FakeResponses(_report_json(), errors=[transient])
    sleeps: list[float] = []
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses),
        retries=1,
        sleep=sleeps.append,
    )

    service.generate_report(_request(tmp_path))
    assert len(responses.calls) == 2
    assert sleeps


def test_compatible_stream_retries_incomplete_protocol_response(tmp_path: Path) -> None:
    from httpx import RemoteProtocolError
    from runtime.responses_damage_report import ResponsesReportService

    class ProtocolChat:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                def broken_stream():
                    raise RemoteProtocolError("incomplete chunked read")
                    yield None

                return broken_stream()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=_report_json()))]
            )

    chat = ProtocolChat()
    service = ResponsesReportService(
        api_key="test-key",
        base_url="https://compatible.example/v1",
        client=_FakeClient(_FakeResponses(), chat),
        retries=1,
        sleep=lambda _seconds: None,
    )

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary
    assert len(chat.calls) == 2


def test_exhausted_incomplete_protocol_streams_are_bounded_and_redacted(
    tmp_path: Path,
) -> None:
    from httpx import RemoteProtocolError
    from runtime.responses_damage_report import ReportGenerationError, ResponsesReportService

    api_key = "test-secret-key"

    class BrokenProtocolChat:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)

            def broken_stream():
                raise RemoteProtocolError(f"incomplete stream using {api_key}")
                yield None

            return broken_stream()

    chat = BrokenProtocolChat()
    service = ResponsesReportService(
        api_key=api_key,
        base_url="https://compatible.example/v1",
        client=_FakeClient(_FakeResponses(), chat),
        retries=2,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ReportGenerationError) as error:
        service.generate_report(_request(tmp_path))

    message = str(error.value)
    assert len(chat.calls) == 3
    assert "3 attempts" in message
    assert api_key not in message
    assert "<redacted>" in message


def test_responses_service_retries_openai_connection_error(tmp_path: Path) -> None:
    from openai import APIConnectionError
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses(_report_json(), errors=[APIConnectionError(request=None)])
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses),
        retries=1,
        sleep=lambda _seconds: None,
    )

    report = service.generate_report(_request(tmp_path))

    assert report.executive_summary
    assert len(responses.calls) == 2


def test_responses_service_retries_wrapped_connection_error(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    try:
        try:
            raise ConnectionError("TLS handshake reset")
        except ConnectionError as cause:
            raise RuntimeError("SDK wrapper") from cause
    except RuntimeError as wrapped:
        transient = wrapped

    responses = _FakeResponses(_report_json(), errors=[transient])
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses),
        retries=1,
        sleep=lambda _seconds: None,
    )

    service.generate_report(_request(tmp_path))

    assert len(responses.calls) == 2


def test_exhausted_openai_connection_errors_include_cause_and_redact_key(
    tmp_path: Path,
) -> None:
    from openai import APIConnectionError
    from runtime.responses_damage_report import ReportGenerationError, ResponsesReportService

    api_key = "test-secret-key"
    errors = []
    for _ in range(3):
        try:
            try:
                raise OSError(f"TLS failure using {api_key}")
            except OSError as cause:
                raise APIConnectionError(request=None) from cause
        except APIConnectionError as wrapped:
            errors.append(wrapped)

    responses = _FakeResponses(errors=errors)
    service = ResponsesReportService(
        api_key=api_key,
        client=_FakeClient(responses),
        retries=2,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ReportGenerationError) as error:
        service.generate_report(_request(tmp_path))

    message = str(error.value)
    assert "3 attempts" in message
    assert "OSError: TLS failure" in message
    assert api_key not in message
    assert "<redacted>" in message


def test_responses_service_uses_three_total_attempts_for_server_errors(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    class ServerError(RuntimeError):
        status_code = 500

    responses = _FakeResponses(_report_json(), errors=[ServerError("first"), ServerError("second")])
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses),
        retries=2,
        sleep=lambda _seconds: None,
    )
    report = service.generate_report(_request(tmp_path))
    assert report.executive_summary
    assert len(responses.calls) == 3


def test_exhausted_server_errors_report_status_and_attempts(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ReportGenerationError, ResponsesReportService

    class ServerError(RuntimeError):
        status_code = 500

    responses = _FakeResponses(errors=[ServerError("one"), ServerError("two"), ServerError("three")])
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses),
        retries=2,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ReportGenerationError, match="HTTP 500.*3 attempts"):
        service.generate_report(_request(tmp_path))


def test_responses_service_rejects_invalid_model_json(tmp_path: Path) -> None:
    from pydantic import ValidationError
    from runtime.responses_damage_report import ResponsesReportService

    responses = _FakeResponses('{"not_a_report": true}')
    service = ResponsesReportService(
        api_key="test-key",
        client=_FakeClient(responses),
    )

    with pytest.raises(ValidationError):
        service.generate_report(_request(tmp_path))


def test_generate_report_from_summary_persists_json_and_markdown_without_key(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    visual = _visual_evidence(tmp_path, "beam.jpg")[0]
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "project_overview": "地下室设备机房巡检。",
                "results": [{
                    "image_name": "beam.jpg",
                    "image_path": visual.image_path,
                    "overlay_path": visual.overlay_path,
                    "status": "success",
                    "damage_findings": [_finding()],
                }],
                "model_provenance": {"model_filename": "best.pt"},
                "inference_provenance": {"severity_rule_version": "1.0.0"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = ResponsesReportService(
        api_key="super-secret",
        client=_FakeClient(_FakeResponses(_report_json())),
    )
    service.generate_report_from_summary(summary_path)

    assert (tmp_path / "report.json").exists()
    persisted = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert persisted["report_schema_version"] == "damage-report.v3"
    assert persisted["generation_audit"]["generation_mode"] == "remote_vision_ai"
    assert persisted["generation_audit"]["model"] == "gpt-5.6-sol"
    assert persisted["generation_audit"]["knowledge_base_used"] is False
    assert len(persisted["generation_audit"]["visual_attachments"]) == 2
    assert persisted["report"]["subject"]["project_overview"] == "地下室设备机房巡检。"
    assert persisted["evidence_snapshot"]["detection_hints"][0]["detected_damage_type"] == "Structural crack"
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "super-secret" not in markdown
    assert "损伤分析报告" in markdown
    assert "**生成方式**：远程视觉 AI" in markdown
    assert "**知识库增强**：未使用" in markdown


def test_report_request_carries_project_overview_from_summary(tmp_path: Path) -> None:
    from runtime.damage_report_schema import DamageReportRequest

    summary = {
        "project_overview": "某办公楼梁柱表面巡检，重点关注裂缝和剥落。",
        "results": [{"image_name": "beam.jpg", "damage_findings": [_finding()]}],
    }
    request = DamageReportRequest.from_summary(summary, str(tmp_path / "summary.json"))

    assert request.subject.project_overview == summary["project_overview"]
    assert request.prompt_payload()["project_overview"] == summary["project_overview"]


def test_remote_report_sends_project_overview_without_overwriting_ai_output(tmp_path: Path) -> None:
    from runtime.responses_damage_report import ResponsesReportService

    overview = " 住宅楼地下室含地下车库和设备机房，涉及防火分区及抗震作用。\n保留本行。 "
    request = _request(tmp_path)
    request.subject.project_overview = overview
    responses = _FakeResponses(_report_json())
    service = ResponsesReportService(
        api_key="test-key",
        model="test-model",
        client=_FakeClient(responses),
        sleep=lambda _seconds: None,
    )

    report = service.generate_report(request)
    submitted = _responses_payload(responses.calls[0])
    assert submitted["project_overview"] == overview
    assert report.subject.project_overview is None


def test_local_fallback_report_persists_structured_evidence(tmp_path: Path) -> None:
    from runtime.responses_damage_report import build_local_fallback_report_from_summary

    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps({"results": [{"image_name": "beam.jpg", "damage_findings": [_finding()]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    report = build_local_fallback_report_from_summary(
        summary_path,
        output_dir=tmp_path,
        reason="HTTP 500 after 3 attempts",
    )
    assert report.provenance.model == "local-evidence-fallback"
    assert report.review_status == "pending_human_review"
    assert report.findings[0].damage_level == "undetermined"
    assert report.findings[0].damage_type == "Structural crack"
    assert report.findings[0].image_name == "beam.jpg"
    persisted = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert persisted["fallback"] is True
    assert persisted["generation_audit"]["generation_mode"] == "local_no_vision_fallback"
    assert persisted["generation_audit"]["knowledge_base_used"] is False
    assert "HTTP 500" in persisted["fallback_reason"]


def test_local_fallback_never_assigns_damage_levels_from_detection_type(tmp_path: Path) -> None:
    from runtime.responses_damage_report import build_local_fallback_report_from_summary

    corrosion = json.loads(json.dumps(_finding()))
    corrosion["index"] = 1
    corrosion["class_id"] = 8
    corrosion["class_name"] = "Rebar corrosion"
    corrosion["crack_geometry"]["applicable"] = False
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {"results": [{"image_name": "beam.jpg", "damage_findings": [_finding(), corrosion]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_local_fallback_report_from_summary(
        summary_path,
        output_dir=tmp_path,
        reason="remote unavailable",
    )

    crack, rebar = report.findings
    assert crack.damage_level == "undetermined"
    assert rebar.damage_level == "undetermined"
    assert crack.risk_interpretation == rebar.risk_interpretation
    assert "像素" not in crack.observed_evidence


def test_local_fallback_report_preserves_and_applies_project_overview(tmp_path: Path) -> None:
    from runtime.responses_damage_report import build_local_fallback_report_from_summary

    overview = "住宅楼地下室包含地下车库和设备机房，涉及消防及荷载传递。"
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {"project_overview": overview, "results": [{"image_name": "beam.jpg", "damage_findings": [_finding()]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_local_fallback_report_from_summary(
        summary_path,
        output_dir=tmp_path,
        reason="remote unavailable",
    )
    persisted = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    combined_limitations = " ".join(report.limitations)

    assert report.subject.project_overview == overview
    assert persisted["report"]["subject"]["project_overview"] == overview
    assert "地下空间" in combined_limitations
    assert "车辆" in combined_limitations
    assert "设备机房" in combined_limitations
    assert "消防" in combined_limitations
    assert "地下空间" in report.executive_summary
    assert overview in markdown


def test_local_fallback_report_redacts_api_key_from_artifacts(tmp_path: Path) -> None:
    from runtime.responses_damage_report import build_local_fallback_report_from_summary

    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps({"results": [{"image_name": "beam.jpg", "damage_findings": [_finding()]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    build_local_fallback_report_from_summary(
        summary_path,
        output_dir=tmp_path,
        reason="provider rejected sk-sensitive-value-123",
    )

    assert "sk-sensitive-value-123" not in (tmp_path / "report.json").read_text(encoding="utf-8")


def test_missing_api_key_fails_before_client_creation(monkeypatch) -> None:
    from runtime.responses_damage_report import ReportConfigurationError, ResponsesReportService

    monkeypatch.delenv("DAMAGE_REPORT_API_KEY", raising=False)
    with pytest.raises(ReportConfigurationError, match="DAMAGE_REPORT_API_KEY"):
        ResponsesReportService()


def test_normalize_responses_base_url_accepts_root_or_endpoint() -> None:
    from runtime.responses_damage_report import normalize_responses_base_url

    assert normalize_responses_base_url("https://www.fhl.mom") == "https://www.fhl.mom/v1"
    assert normalize_responses_base_url("https://www.fhl.mom/v1") == "https://www.fhl.mom/v1"
    assert normalize_responses_base_url("https://www.fhl.mom/v1/responses") == "https://www.fhl.mom/v1"
