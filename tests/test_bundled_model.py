from __future__ import annotations

import hashlib
import json
import sys

import pytest


def test_resource_root_prefers_pyinstaller_meipass(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from runtime.bundled_model import application_resource_root

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert application_resource_root() == tmp_path


def test_resolve_bundled_model_paths_uses_supplied_resource_root(tmp_path) -> None:
    from runtime.bundled_model import resolve_bundled_model_paths

    paths = resolve_bundled_model_paths(tmp_path)

    assert paths.pt == tmp_path / "models" / "best.pt"


def test_packaged_model_manifest_validates_damage_pt(tmp_path) -> None:
    from runtime.bundled_model import validate_packaged_model_artifacts

    models = tmp_path / "models"
    models.mkdir()
    pt = models / "best.pt"
    pt.write_bytes(b"pt-artifact")
    manifest = {
        "artifacts": {
            "pt": {
                "filename": pt.name,
                "size_bytes": pt.stat().st_size,
                "sha256": hashlib.sha256(pt.read_bytes()).hexdigest(),
            }
        }
    }
    (models / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    paths = validate_packaged_model_artifacts(tmp_path)

    assert paths.pt == pt


def test_packaged_model_manifest_rejects_tampered_damage_pt(tmp_path) -> None:
    from runtime.bundled_model import BundledModelError, validate_packaged_model_artifacts

    models = tmp_path / "models"
    models.mkdir()
    pt = models / "best.pt"
    pt.write_bytes(b"pt-artifact")
    manifest = {
        "artifacts": {
            "pt": {
                "filename": pt.name,
                "size_bytes": pt.stat().st_size,
                "sha256": "0" * 64,
            }
        }
    }
    (models / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BundledModelError, match="checksum"):
        validate_packaged_model_artifacts(tmp_path)


def test_source_resolution_uses_damage_pt() -> None:
    from runtime.bundled_model import resolve_bundled_model_paths

    paths = resolve_bundled_model_paths()

    assert paths.pt.as_posix().endswith("models/best.pt")


def test_select_preferred_model_selects_damage_pt(tmp_path) -> None:
    from runtime.bundled_model import resolve_bundled_model_paths, select_preferred_model

    paths = resolve_bundled_model_paths(tmp_path)
    paths.pt.parent.mkdir(parents=True)
    paths.pt.write_bytes(b"pt")
    attempted = []

    selected = select_preferred_model(paths, verifier=lambda path: attempted.append(path))

    assert selected.backend == "pytorch"
    assert selected.path == paths.pt
    assert attempted == [paths.pt]


def test_select_preferred_model_reports_missing_damage_pt(tmp_path) -> None:
    from runtime.bundled_model import BundledModelError, resolve_bundled_model_paths, select_preferred_model

    paths = resolve_bundled_model_paths(tmp_path)

    with pytest.raises(BundledModelError, match="bundled PyTorch YOLO model") as error:
        select_preferred_model(paths)

    assert str(paths.pt) in str(error.value)


def test_load_preferred_runtime_loads_damage_pt_once(tmp_path) -> None:
    from runtime.bundled_model import (
        EXPECTED_DAMAGE_CLASS_NAMES,
        EXPECTED_P2_STRIDES,
        load_preferred_runtime,
        resolve_bundled_model_paths,
    )

    paths = resolve_bundled_model_paths(tmp_path)
    paths.pt.parent.mkdir(parents=True)
    paths.pt.write_bytes(b"pt")
    created = []

    class FakeRuntime:
        def __init__(self, path) -> None:
            self.path = path

        def _ensure_model(self) -> object:
            return type(
                "FakeFormalModel",
                (),
                {
                    "task": "segment",
                    "names": dict(enumerate(EXPECTED_DAMAGE_CLASS_NAMES)),
                    "stride": EXPECTED_P2_STRIDES,
                },
            )()

    runtime, selected = load_preferred_runtime(
        paths, lambda path: created.append(path) or FakeRuntime(path)
    )

    assert runtime.path == paths.pt
    assert selected.backend == "pytorch"
    assert created == [paths.pt]


def test_production_device_resolution_prefers_cuda_zero() -> None:
    from runtime.bundled_model import resolve_production_device

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def get_device_name(_index: int) -> str:
            return "Test GPU"

    class FakeTorch:
        cuda = FakeCuda()

    device = resolve_production_device(FakeTorch())

    assert device.value == "0"
    assert device.is_cuda is True
    assert device.label == "GPU 0: Test GPU"


def test_production_device_resolution_reports_cpu_fallback() -> None:
    from runtime.bundled_model import resolve_production_device

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakeTorch:
        cuda = FakeCuda()

    device = resolve_production_device(FakeTorch())

    assert device.value == "cpu"
    assert device.is_cuda is False
    assert "CPU" in device.label


def test_production_runtime_receives_resolved_cuda_device(tmp_path) -> None:
    from runtime.bundled_model import InferenceDevice, create_production_runtime

    runtime = create_production_runtime(
        tmp_path / "best.pt",
        InferenceDevice(value="0", label="GPU 0: Test GPU", is_cuda=True),
    )

    assert runtime.quality_config.device == "0"


def test_load_preferred_runtime_exposes_device_diagnostics(tmp_path) -> None:
    from runtime.bundled_model import InferenceDevice, load_preferred_runtime, resolve_bundled_model_paths

    paths = resolve_bundled_model_paths(tmp_path)
    paths.pt.parent.mkdir(parents=True)
    paths.pt.write_bytes(b"pt")
    device = InferenceDevice(value="0", label="GPU 0: Test GPU", is_cuda=True)

    _runtime, selected = load_preferred_runtime(
        paths, lambda _path: object(), device=device
    )

    assert selected.execution_device == "0"
    assert selected.device_label == "GPU 0: Test GPU"


def test_segmentation_model_contract_rejects_wrong_task() -> None:
    from runtime.bundled_model import BundledModelError, validate_segmentation_model_contract

    class FakeModel:
        task = "detect"
        names = {0: "damage"}

    with pytest.raises(BundledModelError, match="segmentation"):
        validate_segmentation_model_contract(FakeModel())


def test_segmentation_model_contract_accepts_eight_class_model() -> None:
    from runtime.bundled_model import (
        EXPECTED_DAMAGE_CLASS_NAMES,
        EXPECTED_P2_STRIDES,
        validate_segmentation_model_contract,
    )

    class FakeModel:
        task = "segment"
        names = dict(enumerate(EXPECTED_DAMAGE_CLASS_NAMES))
        stride = EXPECTED_P2_STRIDES

    validate_segmentation_model_contract(FakeModel())


def test_segmentation_model_contract_rejects_wrong_class_order() -> None:
    from runtime.bundled_model import (
        BundledModelError,
        EXPECTED_DAMAGE_CLASS_NAMES,
        EXPECTED_P2_STRIDES,
        validate_segmentation_model_contract,
    )

    class FakeModel:
        task = "segment"
        names = dict(enumerate(reversed(EXPECTED_DAMAGE_CLASS_NAMES)))
        stride = EXPECTED_P2_STRIDES

    with pytest.raises(BundledModelError, match="class order"):
        validate_segmentation_model_contract(FakeModel())


def test_segmentation_model_contract_rejects_model_without_p2_stride() -> None:
    from runtime.bundled_model import (
        BundledModelError,
        EXPECTED_DAMAGE_CLASS_NAMES,
        validate_segmentation_model_contract,
    )

    class FakeModel:
        task = "segment"
        names = dict(enumerate(EXPECTED_DAMAGE_CLASS_NAMES))
        stride = (8.0, 16.0, 32.0)

    with pytest.raises(BundledModelError, match="P2 strides"):
        validate_segmentation_model_contract(FakeModel())


def test_project_formal_model_matches_deployment_manifest() -> None:
    from pathlib import Path

    from ultralytics import YOLO

    from runtime.bundled_model import validate_segmentation_model_contract

    project_root = Path(__file__).resolve().parents[1]
    model_path = project_root / "models" / "best.pt"
    manifest = json.loads(
        (project_root / "deployment" / "models" / "model_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    expected = manifest["artifacts"]["pt"]

    assert model_path.stat().st_size == expected["size_bytes"]
    assert hashlib.sha256(model_path.read_bytes()).hexdigest() == expected["sha256"]
    validate_segmentation_model_contract(YOLO(str(model_path)))
