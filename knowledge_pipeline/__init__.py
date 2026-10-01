"""Independent, auditable knowledge-base processing stages.

The package exposes six deliberately small stages:

``pdf_convert`` -> ``chunk`` -> ``index`` -> ``retrieve`` -> ``rerank`` -> ``generate``.

Each stage can be imported by the desktop application or run as a module with
``python -m knowledge_pipeline.<stage>``.  The shared contracts live in
``contracts.py`` so intermediate JSON files remain inspectable and versioned.
"""

from .contracts import (
    CHUNK_SCHEMA_VERSION,
    CONVERSION_SCHEMA_VERSION,
    GENERATION_SCHEMA_VERSION,
    INDEX_SCHEMA_VERSION,
    RERANK_SCHEMA_VERSION,
    RETRIEVAL_SCHEMA_VERSION,
    BlockRecord,
    ChunkRecord,
    DocumentConversion,
    GenerationContext,
    RetrievalResult,
    RerankResult,
    StageStatus,
)

__all__ = [
    "BlockRecord",
    "ChunkRecord",
    "DocumentConversion",
    "GenerationContext",
    "RetrievalResult",
    "RerankResult",
    "StageStatus",
    "CONVERSION_SCHEMA_VERSION",
    "CHUNK_SCHEMA_VERSION",
    "INDEX_SCHEMA_VERSION",
    "RETRIEVAL_SCHEMA_VERSION",
    "RERANK_SCHEMA_VERSION",
    "GENERATION_SCHEMA_VERSION",
]
