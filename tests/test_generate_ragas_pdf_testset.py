from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_ragas_pdf_testset.py"
SPEC = importlib.util.spec_from_file_location("generate_ragas_pdf_testset", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_hash_embeddings_are_deterministic_and_normalized() -> None:
    model = MODULE.ChineseHashEmbeddings(64)
    first = model.embed_query("混凝土裂缝宽度 0.3 mm")
    second = model.embed_query("混凝土裂缝宽度 0.3 mm")
    assert first == second
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


def test_project_candidate_is_review_only_and_preserves_provenance() -> None:
    unit = MODULE.DocumentUnit(
        unit_id="doc:ragas-unit:1", document_id="doc", source_name="a.pdf",
        source_path="C:/a.pdf", source_sha256="abc", text="来源文档：a.pdf\n裂缝宽度为0.3mm",
        chunk_ids=["c1"], pages=[3], source_markers=["[KB:doc:page:3]"], headings=["第五章"],
    )
    row = MODULE.project_candidate(1, {
        "user_input": "裂缝宽度是多少？", "reference": "0.3mm",
        "reference_contexts": ["裂缝宽度为0.3mm"], "synthesizer_name": "single_hop_specific_query_synthesizer",
    }, [unit])
    assert row["annotation_status"] == "needs_human_review"
    assert row["gold_label"] is False
    assert row["candidate_relevant_chunk_ids"] == ["c1"]
    assert row["evidence"][0]["page_numbers"] == [3]


def test_quota_is_stratified_and_smoke_is_one() -> None:
    assert MODULE.quota_for("混凝土结构设计规范.pdf") == 75
    assert MODULE.quota_for("水下混凝土病害.pdf") == 50
    assert MODULE.quota_for("信息化技术.pdf") == 25
    assert MODULE.quota_for("任何.pdf", smoke=True) == 1
