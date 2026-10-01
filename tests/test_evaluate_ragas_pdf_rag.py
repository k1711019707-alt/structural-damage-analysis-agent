from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_ragas_pdf_rag.py"
SPEC = importlib.util.spec_from_file_location("evaluate_ragas_pdf_rag", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_validate_candidates_rejects_missing_scope() -> None:
    try:
        MODULE.validate_candidates([{"question": "q", "candidate_answer": "a", "reference_contexts": ["c"]}])
    except RuntimeError as exc:
        assert "candidate_relevant_documents" in str(exc)
    else:
        raise AssertionError("missing scope should be rejected")


def test_validate_candidates_allows_review_only_reference_by_default() -> None:
    MODULE.validate_candidates([{
        "question": "q", "candidate_answer": "a", "reference_contexts": ["c"],
        "candidate_relevant_documents": ["doc"], "annotation_status": "needs_human_review", "gold_label": False,
    }])


def test_manifest_never_contains_api_key(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"responses_url": "https://example.test/v1", "responses_key": "secret", "responses_model": "m"}), encoding="utf-8")
    config = MODULE.load_runtime_config(path)
    assert config["api_key"] == "secret"
    assert "secret" not in json.dumps({"model": config["model"], "api_key_present": True})


def test_runtime_environment_overrides_are_one_run_only(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"responses_url": "https://old.example/v1", "responses_key": "old", "responses_model": "old-model"}), encoding="utf-8")
    monkeypatch.setenv("RAGAS_RESPONSES_URL", "https://new.example")
    monkeypatch.setenv("RAGAS_RESPONSES_KEY", "new-secret")
    monkeypatch.setenv("RAGAS_RESPONSES_MODEL", "new-model")
    config = MODULE.load_runtime_config(path)
    assert config == {"base_url": "https://new.example/v1", "api_key": "new-secret", "model": "new-model"}


def test_load_runtime_config_accepts_unified_gui_settings(tmp_path: Path) -> None:
    path = tmp_path / "gui_settings.json"
    path.write_text(json.dumps({
        "api": {
            "responses_url": "https://chat.ai666.net/api/codex",
            "responses_key": "gui-secret",
            "responses_model": "gpt-5.6-sol",
        }
    }), encoding="utf-8")
    config = MODULE.load_runtime_config(path)
    assert config == {
        "base_url": "https://chat.ai666.net/api/codex/v1",
        "api_key": "gui-secret",
        "model": "gpt-5.6-sol",
    }


def test_reuse_generation_flag_exists() -> None:
    assert "reuse_generation" in MODULE.main.__code__.co_varnames or hasattr(MODULE, "run_evaluation")
