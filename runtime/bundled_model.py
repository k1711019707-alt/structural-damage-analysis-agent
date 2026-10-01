from __future__ import annotations

import sys
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from runtime.app_paths import application_resource_root


@dataclass(frozen=True)
class BundledModelPaths:
    pt: Path


@dataclass(frozen=True)
class SelectedBundledModel:
    path: Path
    backend: str
    execution_device: str | None = None
    device_label: str | None = None


@dataclass(frozen=True)
class InferenceDevice:
    """Resolved production device used by the packaged YOLO runtime."""

    value: str
    label: str
    is_cuda: bool


class BundledModelError(RuntimeError):
    pass


EXPECTED_DAMAGE_CLASS_NAMES = (
    "Concrete crushing",
    "Delamination",
    "Microcrack",
    "Minor spalling",
    "Moderate spalling",
    "Rebar corrosion",
    "Structural crack",
    "Structural deformation",
)
EXPECTED_P2_STRIDES = (4.0, 8.0, 16.0, 32.0)


def _ordered_model_names(names: object) -> tuple[str, ...] | None:
    if isinstance(names, dict):
        try:
            return tuple(str(names[index]) for index in range(len(names)))
        except (KeyError, TypeError):
            return None
    if isinstance(names, (list, tuple)):
        return tuple(str(name) for name in names)
    return None


def _model_strides(model: object) -> tuple[float, ...] | None:
    strides = getattr(model, "stride", None)
    if strides is None:
        strides = getattr(getattr(model, "model", None), "stride", None)
    if strides is None:
        return None
    if hasattr(strides, "detach"):
        strides = strides.detach()
    if hasattr(strides, "cpu"):
        strides = strides.cpu()
    if hasattr(strides, "tolist"):
        strides = strides.tolist()
    try:
        return tuple(float(value) for value in strides)
    except (TypeError, ValueError):
        return None


def validate_segmentation_model_contract(
    model: object,
    *,
    expected_class_names: tuple[str, ...] = EXPECTED_DAMAGE_CLASS_NAMES,
    expected_strides: tuple[float, ...] = EXPECTED_P2_STRIDES,
) -> None:
    """Reject a checkpoint that does not match the formal P2 damage model."""
    task = getattr(model, "task", None)
    if task is None:
        task = getattr(getattr(model, "model", None), "task", None)
    if task is not None and str(task).casefold() not in {"segment", "segmentation"}:
        raise BundledModelError(f"Bundled model task is {task!r}, expected segmentation")
    names = getattr(model, "names", None)
    if names is None:
        names = getattr(getattr(model, "model", None), "names", None)
    ordered_names = _ordered_model_names(names)
    if ordered_names != expected_class_names:
        raise BundledModelError(
            "Bundled model class order does not match the formal damage taxonomy: "
            f"{ordered_names!r}"
        )
    strides = _model_strides(model)
    if strides != expected_strides:
        raise BundledModelError(
            f"Bundled model strides are {strides!r}, expected P2 strides {expected_strides!r}"
        )


BUNDLED_MODEL_DIRECTORY = "models"
BUNDLED_PT_FILENAME = "best.pt"
BUNDLED_MODEL_MANIFEST_FILENAME = "model_manifest.json"


def resolve_bundled_model_paths(resource_root: str | Path | None = None) -> BundledModelPaths:
    root = Path(resource_root) if resource_root is not None else application_resource_root()
    return BundledModelPaths(
        # Source and PyInstaller runtimes share the same product-named resource path.
        pt=root / BUNDLED_MODEL_DIRECTORY / BUNDLED_PT_FILENAME,
    )


def bundled_model_manifest_path(resource_root: str | Path | None = None) -> Path:
    """Return the manifest placed beside packaged model artifacts."""
    root = Path(resource_root) if resource_root is not None else application_resource_root()
    if resource_root is None and not bool(getattr(sys, "frozen", False)):
        root = Path(__file__).resolve().parents[1] / "deployment"
    return root / BUNDLED_MODEL_DIRECTORY / BUNDLED_MODEL_MANIFEST_FILENAME


def validate_packaged_model_artifacts(resource_root: str | Path | None = None) -> BundledModelPaths:
    """Verify that the copied executable contains intact trained YOLO11 artifacts."""
    paths = resolve_bundled_model_paths(resource_root)
    manifest_path = bundled_model_manifest_path(resource_root)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BundledModelError(f"Packaged model manifest is unavailable: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise BundledModelError(f"Packaged model manifest is invalid: {manifest_path}") from exc

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise BundledModelError("Packaged model manifest does not define artifacts")

    artifact_name = "pt"
    path = paths.pt
    expected = artifacts.get(artifact_name)
    if not isinstance(expected, dict):
        raise BundledModelError(f"Packaged model manifest is missing {artifact_name} metadata")
    if path.name != expected.get("filename"):
        raise BundledModelError(f"Packaged {artifact_name} filename does not match its manifest")
    if not path.is_file():
        raise BundledModelError(f"Packaged {artifact_name} artifact is missing: {path}")
    expected_size = expected.get("size_bytes")
    if not isinstance(expected_size, int) or path.stat().st_size != expected_size:
        raise BundledModelError(f"Packaged {artifact_name} artifact size validation failed: {path.name}")
    expected_digest = expected.get("sha256")
    if not isinstance(expected_digest, str):
        raise BundledModelError(f"Packaged {artifact_name} artifact checksum is missing")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest.lower() != expected_digest.lower():
        raise BundledModelError(f"Packaged {artifact_name} artifact checksum validation failed: {path.name}")
    return paths


def select_preferred_model(
    paths: BundledModelPaths,
    *,
    verifier: Callable[[Path], object] | None = None,
) -> SelectedBundledModel:
    verify = verifier or _verify_exists
    try:
        verify(paths.pt)
        return SelectedBundledModel(path=paths.pt, backend="pytorch")
    except Exception as exc:
        raise BundledModelError(f"Unable to load bundled PyTorch YOLO model ({paths.pt}): {exc}") from exc


def _verify_exists(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError("model artifact does not exist")


def resolve_production_device(torch_module: object | None = None) -> InferenceDevice:
    """Prefer the first CUDA device while retaining an explicit CPU fallback."""

    try:
        torch = torch_module
        if torch is None:
            import torch as imported_torch

            torch = imported_torch
        cuda = getattr(torch, "cuda")
        if bool(cuda.is_available()):
            return InferenceDevice(
                value="0",
                label=f"GPU 0: {cuda.get_device_name(0)}",
                is_cuda=True,
            )
    except Exception:
        return InferenceDevice(
            value="cpu",
            label="CPU fallback (PyTorch/CUDA unavailable)",
            is_cuda=False,
        )
    return InferenceDevice(
        value="cpu",
        label="CPU fallback (CUDA unavailable)",
        is_cuda=False,
    )


def create_production_runtime(model_path: str | Path, device: InferenceDevice) -> object:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    return YoloSegmentationRuntime(model_path=model_path, device=device.value)


def load_preferred_runtime(
    paths: BundledModelPaths | None = None,
    runtime_factory: Callable[[Path], object] | None = None,
    *,
    device: InferenceDevice | None = None,
) -> tuple[object, SelectedBundledModel]:
    resolved_paths = paths or (
        validate_packaged_model_artifacts()
        if bool(getattr(sys, "frozen", False))
        else resolve_bundled_model_paths()
    )
    resolved_device = device or resolve_production_device()
    if runtime_factory is None:
        runtime_factory = lambda path: create_production_runtime(path, resolved_device)

    loaded: dict[Path, object] = {}

    def verify(path: Path) -> None:
        _verify_exists(path)
        runtime = runtime_factory(path)
        ensure_model = getattr(runtime, "_ensure_model", None)
        if callable(ensure_model):
            validate_segmentation_model_contract(ensure_model())
        loaded[path] = runtime

    selected = select_preferred_model(resolved_paths, verifier=verify)
    return loaded[selected.path], replace(
        selected,
        execution_device=resolved_device.value,
        device_label=resolved_device.label,
    )
