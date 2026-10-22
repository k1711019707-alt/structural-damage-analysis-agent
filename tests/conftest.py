from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def qdrant_client(monkeypatch):
    """Exercise real Qdrant APIs without a shared server or model download."""
    from qdrant_client import QdrantClient
    from knowledge_pipeline import qdrant_store

    client = QdrantClient(":memory:")
    # Factory users normally own a separate HTTP client. This fixture shares
    # one in-memory database across health/build/query calls within a test.
    close = client.close
    monkeypatch.setattr(client, "close", lambda: None)
    monkeypatch.setattr(qdrant_store, "client_from_env", lambda: client)
    yield client
    close()
