"""Versioned JSON-safe contracts shared by knowledge-pipeline stages."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


CONVERSION_SCHEMA_VERSION = "knowledge-conversion.v3"
CHUNK_SCHEMA_VERSION = "knowledge-chunks.v2"
INDEX_SCHEMA_VERSION = "knowledge-index.v2"
RETRIEVAL_SCHEMA_VERSION = "knowledge-retrieval.v1"
SEMANTIC_RETRIEVAL_SCHEMA_VERSION = "knowledge-semantic-retrieval.v1"
RERANK_SCHEMA_VERSION = "knowledge-rerank.v1"
GENERATION_SCHEMA_VERSION = "knowledge-generation.v2"


@dataclass
class StageStatus:
    status: str = "ready"
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    stage_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BlockRecord:
    block_id: str
    location: str
    text: str
    block_type: str = "paragraph"
    page_number: int | None = None
    extraction_method: str = "native"
    confidence: float | None = None
    bbox: list[float] | None = None
    parent_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageRecord:
    """Page inventory and extraction quality facts."""

    page_number: int
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0
    page_type: str = "unknown"
    routing_type: str = "unknown"
    extraction_method: str = "none"
    native_text_chars: int = 0
    native_text_blocks: int = 0
    hidden_text_chars: int = 0
    docling_text_chars: int = 0
    text_source: str = "none"
    ocr_text_chars: int = 0
    image_count: int = 0
    drawing_count: int = 0
    table_candidate_count: int = 0
    image_area_ratio: float = 0.0
    text_density: float = 0.0
    encoding_anomaly: bool = False
    layout_complexity: float = 0.0
    foreground_ratio: float = 0.0
    significant_component_count: int = 0
    noise_component_count: int = 0
    classification_reason: str = ""
    has_table_candidate: bool = False
    has_figure_candidate: bool = False
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)
    blank_review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImageRecord:
    image_id: str
    page_number: int
    bbox: list[float] | None = None
    asset_path: str = ""
    width: int = 0
    height: int = 0
    image_hash: str = ""
    ocr_text: str = ""
    vision_raw: str = ""
    vision_summary: str = ""
    vision_search: str = ""
    extraction_method: str = "embedded-image"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TableRecord:
    table_id: str
    page_number: int
    bbox: list[float] | None = None
    html: str = ""
    extraction_method: str = "candidate"
    confidence: float | None = None
    needs_review: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityReport:
    page_count: int = 0
    native_pages: int = 0
    ocr_pages: int = 0
    mixed_pages: int = 0
    table_pages: int = 0
    image_pages: int = 0
    low_quality_pages: list[int] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)
    intentional_blank_pages: list[int] = field(default_factory=list)
    scan_noise_only_pages: list[int] = field(default_factory=list)
    effective_content_pages: int = 0
    successfully_recovered_ocr_pages: list[int] = field(default_factory=list)
    blank_page_reviews: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    optional_backends: dict[str, str] = field(default_factory=dict)
    quality_score: float | None = None
    needs_review: bool = False
    page_metrics: dict[str, Any] = field(default_factory=dict)
    text_metrics: dict[str, Any] = field(default_factory=dict)
    table_metrics: dict[str, Any] = field(default_factory=dict)
    vision_metrics: dict[str, Any] = field(default_factory=dict)
    header_review_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentConversion:
    document_id: str
    source_path: str
    source_name: str
    source_sha256: str
    extension: str
    blocks: list[BlockRecord] = field(default_factory=list)
    pages: list[PageRecord] = field(default_factory=list)
    images: list[ImageRecord] = field(default_factory=list)
    tables: list[TableRecord] = field(default_factory=list)
    visual_regions: list[dict[str, Any]] = field(default_factory=list)
    quality_report: QualityReport = field(default_factory=QualityReport)
    status: StageStatus = field(default_factory=StageStatus)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CONVERSION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.to_dict()
        payload["pages"] = [page.to_dict() for page in self.pages]
        payload["images"] = [image.to_dict() for image in self.images]
        payload["tables"] = [table.to_dict() for table in self.tables]
        payload["visual_regions"] = list(self.visual_regions)
        payload["quality_report"] = self.quality_report.to_dict()
        return payload


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    location: str
    text: str
    parent_id: str = ""
    source_marker: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    query: str
    chunks: list[ChunkRecord] = field(default_factory=list)
    scope_available: bool = False
    retrieval_mode: str = "lexical"
    relevance_status: str = "unknown"
    scope_document_ids: list[str] = field(default_factory=list)
    status: StageStatus = field(default_factory=StageStatus)
    schema_version: str = RETRIEVAL_SCHEMA_VERSION
    anchors: list[dict[str, Any]] = field(default_factory=list)
    context_groups: list[dict[str, Any]] = field(default_factory=list)
    route_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["chunks"] = [chunk.to_dict() for chunk in self.chunks]
        payload["status"] = self.status.to_dict()
        return payload


@dataclass
class SemanticRetrievalResult:
    query: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    scope_available: bool = False
    retrieval_mode: str = "semantic"
    relevance_status: str = "unknown"
    scope_document_ids: list[str] = field(default_factory=list)
    status: StageStatus = field(default_factory=StageStatus)
    schema_version: str = SEMANTIC_RETRIEVAL_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.to_dict()
        return payload


@dataclass
class RerankResult:
    query: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    reranker: str = "deterministic-baseline"
    status: StageStatus = field(default_factory=StageStatus)
    schema_version: str = RERANK_SCHEMA_VERSION
    retrieval_mode: str = "unknown"
    relevance_status: str = "unknown"
    scope_available: bool = False
    scope_document_ids: list[str] = field(default_factory=list)
    anchors: list[dict[str, Any]] = field(default_factory=list)
    context_groups: list[dict[str, Any]] = field(default_factory=list)
    route_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.to_dict()
        return payload


@dataclass
class GenerationContext:
    query: str
    evidence: dict[str, Any] = field(default_factory=dict)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    prompt: str = ""
    generation_mode: str = "context-only"
    review_status: str = "pending_engineer_review"
    status: StageStatus = field(default_factory=StageStatus)
    schema_version: str = GENERATION_SCHEMA_VERSION
    retrieval_mode: str = "unknown"
    relevance_status: str = "unknown"
    scope_available: bool = False
    scope_document_ids: list[str] = field(default_factory=list)
    retrieval_warnings: list[str] = field(default_factory=list)
    anchors: list[dict[str, Any]] = field(default_factory=list)
    context_groups: list[dict[str, Any]] = field(default_factory=list)
    evidence_groups: list[dict[str, Any]] = field(default_factory=list)
    answer_source_mode: str = "knowledge_base"
    external_fallback_reason: str = ""
    external_sources: list[dict[str, Any]] = field(default_factory=list)
    requires_source_citation: bool = False
    knowledge_freshness_warning: str = ""
    engineer_review_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.to_dict()
        return payload


def write_json(path: str, payload: dict[str, Any]) -> None:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: str) -> dict[str, Any]:
    from pathlib import Path

    return json.loads(Path(path).read_text(encoding="utf-8"))


def chunks_from_payload(payload: dict[str, Any]) -> list[ChunkRecord]:
    raw = payload.get("chunks") or []
    return [
        ChunkRecord(
            chunk_id=str(item.get("chunk_id", "")),
            document_id=str(item.get("document_id", "")),
            location=str(item.get("location", "")),
            text=str(item.get("text", "")),
            parent_id=str(item.get("parent_id", "")),
            source_marker=str(item.get("source_marker", "")),
            metadata=dict(item.get("metadata") or {}),
        )
        for item in raw
        if isinstance(item, dict)
    ]
