"""Typed, serializable contracts for the unified settings and generation flow."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


SETTINGS_VERSION = 2
PROFILE_NAMES = ("損傷分析報告", "施工方案")


@dataclass
class ApiSettings:
    responses_url: str = "https://api.openai.com/v1"
    responses_key: str = ""
    responses_model: str = "gpt-5.6-sol"
    fhl_url: str = "https://www.fhl.mom/v1/images/edits"
    fhl_key: str = ""
    repair_render_provider: str = "fhl"
    siliconflow_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_key: str = ""
    siliconflow_model: str = "Qwen/Qwen-Image-Edit-2509"

    def to_legacy(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class KnowledgeBaseSettings:
    root_dir: str = "knowledge_base"
    enabled_document_ids: list[str] = field(default_factory=list)
    max_file_size_mb: int = 50
    max_total_size_mb: int = 500
    chunk_size: int = 1200
    chunk_overlap: int = 160
    top_k: int = 6
    max_context_chars: int = 10000
    ai_tool_rag: bool = True
    max_tool_calls: int = 3


@dataclass
class GenerationProfile:
    name: str
    enabled: bool = True
    prompt: str = ""
    model: str = ""
    max_output_tokens: int = 4000
    temperature: float = 0.2
    knowledge_base_document_ids: list[str] = field(default_factory=list)
    knowledge_base_folder_ids: list[str] = field(default_factory=list)
    template_id: str = ""
    template_required: bool = False
    version: int = 1

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.name not in PROFILE_NAMES:
            errors.append("不支援的生成档案名称")
        if not self.prompt.strip():
            errors.append(f"{self.name} 的提示词不能为空")
        if not 1 <= int(self.max_output_tokens) <= 100000:
            errors.append(f"{self.name} 的输出长度必须在 1 到 100000 之间")
        if not 0.0 <= float(self.temperature) <= 2.0:
            errors.append(f"{self.name} 的温度必须在 0 到 2 之间")
        if self.template_required and not self.template_id:
            errors.append(f"{self.name} 已要求模板，但未选择模板")
        return errors


def default_profiles() -> dict[str, GenerationProfile]:
    return {
        "損傷分析報告": GenerationProfile(
            name="損傷分析報告",
            prompt="按用户要求生成损伤分析报告，并返回应用所需的结构化 JSON。",
        ),
        "施工方案": GenerationProfile(
            name="施工方案",
            prompt=(
                "依人工確認的損傷報告、確定性工法卡及選定知識庫參考，擴寫施工前複核、"
                "材料設備、工序、品質、安全與驗收內容；不得改動損傷事實、工法、尺度、"
                "審核狀態或施工放行狀態。"
            ),
        ),
    }


@dataclass
class AppSettings:
    settings_version: int = SETTINGS_VERSION
    api: ApiSettings = field(default_factory=ApiSettings)
    knowledge_base: KnowledgeBaseSettings = field(default_factory=KnowledgeBaseSettings)
    generation_profiles: dict[str, GenerationProfile] = field(default_factory=default_profiles)
    templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    render_prompt: str = (
        "以输入的原始损伤照片为唯一场景依据，依据已确认的修复施工方案生成一张真实、保守、"
        "可复核的修复后效果图。保持构件几何形态、位置、比例、拍摄视角、透视关系、背景、光照、"
        "材质纹理和变形状态不变；只处理照片中能够确认的可见损伤，不扩大范围，不新增或删除构件、"
        "钢筋、设备、人员、文字、编号、箭头、标注、Logo、水印或边框。不得拉直、校正或掩盖结构变形，"
        "不得虚构尺寸、材料、厚度、强度、工程量、安全等级或施工完成状态。输出仅用于工程沟通和人工复核，"
        "不代表施工放行、验收合格或结构安全鉴定。"
    )
    updated_at: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.settings_version != SETTINGS_VERSION:
            errors.append(f"不支持的设置版本：{self.settings_version}")
        if self.api.repair_render_provider not in {"fhl", "siliconflow"}:
            errors.append("修复渲染平台必须是 FHL 或硅基流动")
        for profile_name in PROFILE_NAMES:
            profile = self.generation_profiles.get(profile_name)
            if profile is None:
                errors.append(f"缺少生成档案：{profile_name}")
            else:
                errors.extend(profile.validate())
        kb = self.knowledge_base
        if kb.chunk_size <= 0 or kb.chunk_overlap < 0 or kb.chunk_overlap >= kb.chunk_size:
            errors.append("知识库分块大小或重叠长度无效")
        if kb.top_k < 1 or kb.max_context_chars < 100 or kb.max_tool_calls < 1:
            errors.append("知识库检索上限无效")
        return errors

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["updated_at"] = self.updated_at or datetime.now(timezone.utc).isoformat()
        return payload

    def to_json(self, *, redact_secrets: bool = False) -> str:
        payload = self.to_dict()
        if redact_secrets:
            payload["api"]["responses_key"] = ""
            payload["api"]["fhl_key"] = ""
            payload["api"]["siliconflow_key"] = ""
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def snapshot(self) -> tuple[str, "AppSettings"]:
        payload = self.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        snapshot_id = hashlib.sha256(encoded).hexdigest()[:16]
        return snapshot_id, AppSettings.from_dict(json.loads(json.dumps(payload, ensure_ascii=False)))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AppSettings":
        api_payload = payload.get("api") or {}
        kb_payload = payload.get("knowledge_base") or {}
        profiles_payload = payload.get("generation_profiles") or {}
        profiles = default_profiles()
        for name, raw in profiles_payload.items():
            if name in profiles and isinstance(raw, dict):
                base = asdict(profiles[name])
                base.update({key: value for key, value in raw.items() if key in base})
                profiles[name] = GenerationProfile(**base)
        api_base = asdict(ApiSettings())
        api_base.update({key: str(value) for key, value in api_payload.items() if key in api_base})
        kb_base = asdict(KnowledgeBaseSettings())
        kb_base.update({key: value for key, value in kb_payload.items() if key in kb_base})
        return cls(
            settings_version=int(payload.get("settings_version", SETTINGS_VERSION)),
            api=ApiSettings(**api_base),
            knowledge_base=KnowledgeBaseSettings(**kb_base),
            generation_profiles=profiles,
            templates=dict(payload.get("templates") or {}),
            render_prompt=str(payload.get("render_prompt", cls().render_prompt)),
            updated_at=str(payload.get("updated_at", "")),
        )

    @classmethod
    def from_legacy(cls, payload: dict[str, Any]) -> "AppSettings":
        settings = cls()
        settings.api = ApiSettings(
            responses_url=str(payload.get("responses_url", settings.api.responses_url)),
            responses_key=str(payload.get("responses_key", "")),
            responses_model=str(payload.get("responses_model", settings.api.responses_model)),
            fhl_url=str(payload.get("fhl_url", settings.api.fhl_url)),
            fhl_key=str(payload.get("fhl_key", "")),
        )
        return settings
