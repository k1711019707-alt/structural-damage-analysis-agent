from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

try:
    from runtime.app_paths import application_resource_root
except ModuleNotFoundError:  # direct ``python runtime/yolo_segmentation_runtime.py``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runtime.app_paths import application_resource_root

# ==================== YOLO 推理参数集中配置 ====================
PROJECT_ROOT = application_resource_root()  # 源码或冻结程序的资源根目录
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
WORKSPACE_ROOT = PROJECT_ROOT.parent  # 海之子工作区的绝对根目录
DEFAULT_MODEL_PATH = (  # 源码与冻结程序只使用随包分发的正式模型
    PROJECT_ROOT / "models" / "best.pt"
).resolve()
DEFAULT_INPUT_PATH = (  # 默认推理输入为划分后的测试集图像目录
    WORKSPACE_ROOT / "dataset_yolo11_seg/images/test"
).resolve()
DEFAULT_OUTPUT_ROOT = (PROJECT_ROOT / "results/inference").resolve()  # 默认推理输出根目录
DEFAULT_OUTPUT_PATH: str | None = None  # 未指定输出目录时按输入名称在输出根目录下自动创建
DEFAULT_DEVICE = "0"  # 默认使用第一块英伟达显卡执行推理
DEFAULT_INFERENCE_IMGSZ = 1280  # 默认推理尺寸，用于保留细小损伤边缘
DEFAULT_CONFIDENCE_THRESHOLD = 0.25  # 默认检测置信度阈值
DEFAULT_IOU_THRESHOLD = 0.7  # 默认非极大值抑制交并比阈值
DEFAULT_RETINA_MASKS = True  # 默认请求原图分辨率的分割掩膜
DEFAULT_MASK_THRESHOLD = 0.5  # 掩膜二值化阈值
DEFAULT_MASK_SMOOTHING = 0  # 掩膜形态学平滑核尺寸，零表示关闭
DEFAULT_KEEP_LARGEST_COMPONENT = True  # 默认仅保留最大连通区域
DEFAULT_FILL_MASK_HOLES = True  # 默认填充掩膜内部封闭孔洞
DEFAULT_HIGH_QUALITY_RENDERING = True  # 默认启用高质量抗锯齿渲染
DEFAULT_INSPECTION_MERGE_IOU = 0.05  # 全量巡检中同类别掩膜的最小合并交并比
DEFAULT_QUIET = False  # 默认显示推理进度信息
# ==================== YOLO 推理参数集中配置结束 ====================

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
RESULT_SCHEMA_VERSION = "damage-finding.v1"
MASK_MEASUREMENT_VERSION = "binary-mask.v1"
CRACK_GEOMETRY_VERSION = "skeleton-distance-transform.v1"
SEVERITY_RULE_ID = "affected-image-area-ratio"
SEVERITY_RULE_VERSION = "1.0.0"
CRACK_CLASS_NAMES = {"microcrack", "structural crack"}
SEVERITY_MEDIUM_AREA_RATIO = 0.01
SEVERITY_HIGH_AREA_RATIO = 0.05

ModelLoader = Callable[[str], Callable[..., Any]]
ImageReader = Callable[[str], np.ndarray | None]
ImageWriter = Callable[[str, np.ndarray], bool | None]
ProgressWriter = Callable[[str], None]


def resolve_project_path(path_value: str | Path | None) -> Path | None:
    if path_value is None:
        return None
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _resolve_image_paths(input_path: str | Path) -> list[Path]:
    resolved_input = Path(input_path)
    if not resolved_input.exists():
        raise FileNotFoundError(f"Input path does not exist: {resolved_input}")

    if resolved_input.is_file():
        if resolved_input.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported image suffix: {resolved_input.suffix}")
        return [resolved_input]

    image_paths = sorted(
        [
            path
            for path in resolved_input.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ],
        key=lambda path: path.name.lower(),
    )
    if not image_paths:
        raise ValueError(f"No supported image files were found under: {resolved_input}")
    return image_paths


def _default_output_dir_for_input(input_path: str | Path) -> Path:
    resolved_input = Path(input_path)
    input_name = (
        resolved_input.stem if resolved_input.is_file() else resolved_input.name
    )
    return DEFAULT_OUTPUT_ROOT / input_name


def _to_numpy_array(value: Any) -> np.ndarray:
    resolved = value
    if hasattr(resolved, "detach"):
        resolved = resolved.detach()
    if hasattr(resolved, "cpu"):
        resolved = resolved.cpu()
    if hasattr(resolved, "numpy"):
        resolved = resolved.numpy()
    return np.asarray(resolved)


def _validate_probability(value: float | None, name: str) -> None:
    if value is not None and not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0, got {value}")


def mask_iou(first_mask: np.ndarray, second_mask: np.ndarray) -> float:
    """Return binary-mask intersection over union for core inspection merging."""
    first = np.asarray(first_mask, dtype=bool)
    second = np.asarray(second_mask, dtype=bool)
    if first.shape != second.shape:
        raise ValueError("mask_iou requires masks with identical shapes")
    union_pixels = int(np.count_nonzero(np.logical_or(first, second)))
    if union_pixels == 0:
        return 1.0
    intersection_pixels = int(np.count_nonzero(np.logical_and(first, second)))
    return float(intersection_pixels / union_pixels)


def build_model_artifact_metadata(model_path: str | Path) -> dict[str, str | None]:
    resolved_path = Path(model_path)
    suffix = resolved_path.suffix.lower() or None
    backend = {
        ".pt": "pytorch",
        ".engine": "tensorrt",
    }.get(suffix, "unknown")
    return {
        "backend": backend,
        "artifact_suffix": suffix,
        "model_filename": resolved_path.name or None,
    }


def _resolve_class_name(
    class_id: int, *name_sources: object
) -> str:
    for names in name_sources:
        if isinstance(names, dict):
            if class_id in names:
                return str(names[class_id])
            if str(class_id) in names:
                return str(names[str(class_id)])
        elif isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
            return str(names[class_id])
    return f"class_{class_id}"


def _mask_area_measurements(
    mask: np.ndarray, *, source_image_shape: Sequence[int]
) -> dict[str, object]:
    binary_mask = np.asarray(mask, dtype=bool)
    height, width = binary_mask.shape[:2]
    source_height, source_width = (int(source_image_shape[0]), int(source_image_shape[1]))
    area_pixels = int(np.count_nonzero(binary_mask))
    measurement_pixels = int(binary_mask.size)
    return {
        "method": MASK_MEASUREMENT_VERSION,
        "area_pixels": area_pixels,
        "measurement_frame_pixels": measurement_pixels,
        "area_ratio": round(
            float(area_pixels / measurement_pixels) if measurement_pixels else 0.0, 8
        ),
        "measurement_width_px": int(width),
        "measurement_height_px": int(height),
        "source_image_width_px": source_width,
        "source_image_height_px": source_height,
    }


def _skeleton_length_pixels(skeleton: np.ndarray) -> float:
    binary = np.asarray(skeleton, dtype=bool)
    horizontal = np.count_nonzero(binary[:, :-1] & binary[:, 1:])
    vertical = np.count_nonzero(binary[:-1, :] & binary[1:, :])
    diagonal_down = np.count_nonzero(binary[:-1, :-1] & binary[1:, 1:])
    diagonal_up = np.count_nonzero(binary[1:, :-1] & binary[:-1, 1:])
    return float(horizontal + vertical + math.sqrt(2.0) * (diagonal_down + diagonal_up))


def _crack_geometry(mask: np.ndarray, class_name: str) -> dict[str, object]:
    if class_name.strip().casefold() not in CRACK_CLASS_NAMES:
        return {
            "applicable": False,
            "method": CRACK_GEOMETRY_VERSION,
            "length_px": None,
            "mean_width_px": None,
            "maximum_width_px": None,
            "physical_length": None,
            "physical_width": None,
            "physical_unit": None,
            "calibration_available": False,
        }

    binary_mask = np.asarray(mask, dtype=bool)
    if not np.any(binary_mask):
        return {
            "applicable": True,
            "method": CRACK_GEOMETRY_VERSION,
            "length_px": 0.0,
            "mean_width_px": None,
            "maximum_width_px": None,
            "physical_length": None,
            "physical_width": None,
            "physical_unit": None,
            "calibration_available": False,
        }

    from scipy.ndimage import distance_transform_edt
    from skimage.morphology import skeletonize

    skeleton = skeletonize(binary_mask)
    length_px = _skeleton_length_pixels(skeleton)
    if length_px <= 0.0:
        length_px = 1.0
    distances = distance_transform_edt(binary_mask)
    skeleton_distances = distances[skeleton]
    maximum_width_px = (
        float(2.0 * skeleton_distances.max())
        if skeleton_distances.size
        else None
    )
    mean_width_px = float(np.count_nonzero(binary_mask) / length_px)
    return {
        "applicable": True,
        "method": CRACK_GEOMETRY_VERSION,
        "length_px": round(length_px, 6),
        "mean_width_px": round(mean_width_px, 6),
        "maximum_width_px": round(maximum_width_px, 6)
        if maximum_width_px is not None
        else None,
        "physical_length": None,
        "physical_width": None,
        "physical_unit": None,
        "calibration_available": False,
    }


def _screening_severity(area_ratio: float, area_pixels: int) -> dict[str, object]:
    if area_pixels <= 0:
        level = "undetermined"
    elif area_ratio < SEVERITY_MEDIUM_AREA_RATIO:
        level = "low"
    elif area_ratio < SEVERITY_HIGH_AREA_RATIO:
        level = "medium"
    else:
        level = "high"
    return {
        "level": level,
        "rule_id": SEVERITY_RULE_ID,
        "rule_version": SEVERITY_RULE_VERSION,
        "observed_area_ratio": round(float(area_ratio), 8),
        "thresholds": {
            "medium_min_area_ratio": SEVERITY_MEDIUM_AREA_RATIO,
            "high_min_area_ratio": SEVERITY_HIGH_AREA_RATIO,
        },
        "basis": "Deterministic screening based on affected image-area ratio.",
        "limitations": (
            "Preliminary screening only; physical scale, component context, "
            "loading, environment, and engineering-code criteria are not included."
        ),
    }


def _build_damage_finding(
    *,
    instance_index: int,
    class_id: int,
    class_name: str,
    score: float,
    box: Sequence[float],
    mask: np.ndarray,
    source_image_shape: Sequence[int],
) -> dict[str, object]:
    area = _mask_area_measurements(mask, source_image_shape=source_image_shape)
    return {
        "index": int(instance_index),
        "class_id": int(class_id),
        "class_name": str(class_name),
        "score": round(float(score), 6),
        "detection_confidence": round(float(score), 6),
        "box": [float(value) for value in box],
        "area": area,
        "crack_geometry": _crack_geometry(mask, class_name),
        "screening_severity": _screening_severity(
            float(area["area_ratio"]), int(area["area_pixels"])
        ),
    }


def _json_safe_finding(instance: dict[str, object] | None) -> dict[str, object] | None:
    if instance is None:
        return None
    return {
        key: value
        for key, value in instance.items()
        if key not in {"mask", "mask_scores"}
    }


@dataclass(frozen=True)
class SegmentationQualityConfig:
    imgsz: int | None = DEFAULT_INFERENCE_IMGSZ
    conf: float | None = DEFAULT_CONFIDENCE_THRESHOLD
    iou: float | None = DEFAULT_IOU_THRESHOLD
    device: str | None = DEFAULT_DEVICE
    retina_masks: bool = DEFAULT_RETINA_MASKS
    mask_threshold: float = DEFAULT_MASK_THRESHOLD
    mask_smoothing: int = DEFAULT_MASK_SMOOTHING
    keep_largest_component: bool = DEFAULT_KEEP_LARGEST_COMPONENT
    fill_mask_holes: bool = DEFAULT_FILL_MASK_HOLES
    high_quality_rendering: bool = DEFAULT_HIGH_QUALITY_RENDERING

    def __post_init__(self) -> None:
        if self.imgsz is not None and int(self.imgsz) <= 0:
            raise ValueError(f"imgsz must be a positive integer, got {self.imgsz}")
        _validate_probability(self.conf, "conf")
        _validate_probability(self.iou, "iou")
        _validate_probability(self.mask_threshold, "mask_threshold")
        if int(self.mask_smoothing) < 0:
            raise ValueError(
                f"mask_smoothing must be a non-negative integer, got {self.mask_smoothing}"
            )

    def model_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "verbose": False,
            "retina_masks": bool(self.retina_masks),
        }
        if self.imgsz is not None:
            kwargs["imgsz"] = int(self.imgsz)
        if self.conf is not None:
            kwargs["conf"] = float(self.conf)
        if self.iou is not None:
            kwargs["iou"] = float(self.iou)
        if self.device is not None:
            kwargs["device"] = self.device
        return kwargs

    def to_summary(self) -> dict[str, object]:
        return asdict(self)


def _normalize_smoothing_kernel(mask_smoothing: int) -> int:
    kernel_size = int(mask_smoothing)
    if kernel_size <= 1:
        return 0
    return kernel_size if kernel_size % 2 == 1 else kernel_size + 1


def _smooth_mask_scores(mask_scores: np.ndarray, mask_smoothing: int) -> np.ndarray:
    kernel_size = _normalize_smoothing_kernel(mask_smoothing)
    if kernel_size == 0:
        return mask_scores

    import cv2

    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    closed_mask = cv2.morphologyEx(mask_scores, cv2.MORPH_CLOSE, kernel)
    return cv2.morphologyEx(closed_mask, cv2.MORPH_OPEN, kernel)


def _keep_largest_connected_component(mask: np.ndarray) -> np.ndarray:
    mask_bool = np.asarray(mask, dtype=bool)
    if not mask_bool.any():
        return mask_bool

    import cv2

    mask_uint8 = mask_bool.astype(np.uint8)
    contours, _hierarchy = cv2.findContours(
        mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if len(contours) <= 1:
        return mask_bool

    largest_contour = max(contours, key=cv2.contourArea)
    largest_mask = np.zeros_like(mask_uint8, dtype=np.uint8)
    cv2.drawContours(
        largest_mask, [largest_contour], contourIdx=-1, color=1, thickness=cv2.FILLED
    )
    return largest_mask.astype(bool)


def _fill_enclosed_mask_holes(mask: np.ndarray) -> np.ndarray:
    mask_bool = np.asarray(mask, dtype=bool)
    if not mask_bool.any():
        return mask_bool

    import cv2

    inverse_mask = (~mask_bool).astype(np.uint8)
    if not inverse_mask.any():
        return mask_bool

    flood_input = inverse_mask.copy()
    height, width = flood_input.shape
    flood_mask = np.zeros((height + 2, width + 2), dtype=np.uint8)

    for x in range(width):
        if flood_input[0, x] == 1:
            cv2.floodFill(flood_input, flood_mask, (x, 0), 0)
        if flood_input[height - 1, x] == 1:
            cv2.floodFill(flood_input, flood_mask, (x, height - 1), 0)
    for y in range(height):
        if flood_input[y, 0] == 1:
            cv2.floodFill(flood_input, flood_mask, (0, y), 0)
        if flood_input[y, width - 1] == 1:
            cv2.floodFill(flood_input, flood_mask, (width - 1, y), 0)

    output = mask_bool.copy()
    output[flood_input.astype(bool)] = True
    return output


def _clean_binary_mask(
    mask: np.ndarray,
    *,
    keep_largest_component: bool,
    fill_mask_holes: bool,
) -> np.ndarray:
    cleaned_mask = np.asarray(mask, dtype=bool)
    if keep_largest_component:
        cleaned_mask = _keep_largest_connected_component(cleaned_mask)
    if fill_mask_holes:
        cleaned_mask = _fill_enclosed_mask_holes(cleaned_mask)
    return cleaned_mask


def _mask_bounding_box(mask: np.ndarray) -> list[float]:
    rows, columns = np.where(np.asarray(mask, dtype=bool))
    if columns.size == 0:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        float(columns.min()),
        float(rows.min()),
        float(columns.max() + 1),
        float(rows.max() + 1),
    ]


def aggregate_inspection_instances(
    instances: Sequence[dict[str, object]],
    *,
    merge_iou: float = DEFAULT_INSPECTION_MERGE_IOU,
) -> list[dict[str, object]]:
    """Merge overlapping same-class instances for full-image inspection."""
    _validate_probability(merge_iou, "merge_iou")
    resolved_instances = list(instances)
    if not resolved_instances:
        return []

    parents = list(range(len(resolved_instances)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for first_index, first in enumerate(resolved_instances):
        first_mask = np.asarray(first["mask"], dtype=bool)
        for second_index in range(first_index + 1, len(resolved_instances)):
            second = resolved_instances[second_index]
            if int(first["class_id"]) != int(second["class_id"]):
                continue
            second_mask = np.asarray(second["mask"], dtype=bool)
            if mask_iou(first_mask, second_mask) >= float(merge_iou):
                union(first_index, second_index)

    groups: dict[int, list[dict[str, object]]] = {}
    for instance_index, instance in enumerate(resolved_instances):
        groups.setdefault(find(instance_index), []).append(instance)

    aggregated: list[dict[str, object]] = []
    for members in groups.values():
        ordered_members = sorted(members, key=lambda item: int(item["index"]))
        best_member = max(ordered_members, key=lambda item: float(item["score"]))
        member_indices = [int(member["index"]) for member in ordered_members]
        if len(ordered_members) == 1:
            singleton = dict(best_member)
            singleton["member_indices"] = member_indices
            singleton["merged_instance_count"] = 1
            aggregated.append(singleton)
            continue

        union_mask = np.logical_or.reduce(
            [np.asarray(member["mask"], dtype=bool) for member in ordered_members]
        )
        union_scores = np.maximum.reduce(
            [np.asarray(member["mask_scores"], dtype=np.float32) for member in ordered_members]
        )
        union_box = _mask_bounding_box(union_mask)
        finding = _build_damage_finding(
            instance_index=int(best_member["index"]),
            class_id=int(best_member["class_id"]),
            class_name=str(best_member["class_name"]),
            score=float(best_member["score"]),
            box=union_box,
            mask=union_mask,
            source_image_shape=union_mask.shape,
        )
        aggregated.append(
            {
                **finding,
                "mask": union_mask,
                "mask_scores": union_scores,
                "member_indices": member_indices,
                "merged_instance_count": len(member_indices),
            }
        )
    return aggregated


def _resize_mask_to_image_shape(
    mask: np.ndarray,
    image: np.ndarray,
    *,
    mask_threshold: float = DEFAULT_MASK_THRESHOLD,
    mask_smoothing: int = DEFAULT_MASK_SMOOTHING,
    keep_largest_component: bool = DEFAULT_KEEP_LARGEST_COMPONENT,
    fill_mask_holes: bool = DEFAULT_FILL_MASK_HOLES,
) -> tuple[np.ndarray, np.ndarray]:
    image_height, image_width = image.shape[:2]
    mask_scores = np.asarray(mask, dtype=np.float32)

    if mask_scores.shape != (image_height, image_width):
        import cv2

        mask_scores = cv2.resize(
            mask_scores,
            (image_width, image_height),
            interpolation=cv2.INTER_LINEAR,
        )

    mask_scores = _smooth_mask_scores(mask_scores, mask_smoothing)
    np.clip(mask_scores, 0.0, 1.0, out=mask_scores)
    binary_mask = mask_scores >= float(mask_threshold)
    return (
        _clean_binary_mask(
            binary_mask,
            keep_largest_component=keep_largest_component,
            fill_mask_holes=fill_mask_holes,
        ),
        mask_scores,
    )


class YoloSegmentationRuntime:
    def __init__(
        self,
        *,
        model_path: str | Path = DEFAULT_MODEL_PATH,
        model_loader: ModelLoader | None = None,
        imgsz: int | None = DEFAULT_INFERENCE_IMGSZ,
        conf: float | None = DEFAULT_CONFIDENCE_THRESHOLD,
        iou: float | None = DEFAULT_IOU_THRESHOLD,
        device: str | None = DEFAULT_DEVICE,
        retina_masks: bool = DEFAULT_RETINA_MASKS,
        mask_threshold: float = DEFAULT_MASK_THRESHOLD,
        mask_smoothing: int = DEFAULT_MASK_SMOOTHING,
        keep_largest_component: bool = DEFAULT_KEEP_LARGEST_COMPONENT,
        fill_mask_holes: bool = DEFAULT_FILL_MASK_HOLES,
        high_quality_rendering: bool = DEFAULT_HIGH_QUALITY_RENDERING,
        inspection_merge_iou: float = DEFAULT_INSPECTION_MERGE_IOU,
    ) -> None:
        self.model_path = str(model_path)
        self._model_loader = model_loader
        self._model = None
        self._model_provenance_cache: dict[str, object] | None = None
        self.quality_config = SegmentationQualityConfig(
            imgsz=imgsz,
            conf=conf,
            iou=iou,
            device=device,
            retina_masks=retina_masks,
            mask_threshold=mask_threshold,
            mask_smoothing=mask_smoothing,
            keep_largest_component=keep_largest_component,
            fill_mask_holes=fill_mask_holes,
            high_quality_rendering=high_quality_rendering,
        )
        _validate_probability(inspection_merge_iou, "inspection_merge_iou")
        self.inspection_merge_iou = float(inspection_merge_iou)

    def _default_model_loader(self, model_path: str):
        from ultralytics import YOLO

        return YOLO(model_path)

    def _default_image_reader(self, image_path: str) -> np.ndarray | None:
        import cv2

        path = Path(image_path)
        try:
            encoded = np.fromfile(path, dtype=np.uint8)
        except OSError:
            return None
        if encoded.size == 0:
            return None
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)

    def _default_image_writer(self, image_path: str, image: np.ndarray) -> bool:
        import cv2

        path = Path(image_path)
        suffix = path.suffix or ".png"
        encoded_ok, encoded = cv2.imencode(suffix, image)
        if not encoded_ok:
            return False
        try:
            encoded.tofile(path)
        except OSError:
            return False
        return True

    def _ensure_model(self):
        if self._model is None:
            loader = self._model_loader or self._default_model_loader
            self._model = loader(self.model_path)
        return self._model

    def get_backend_metadata(self) -> dict[str, str | None]:
        metadata = build_model_artifact_metadata(self.model_path)
        metadata["model_path"] = self.model_path
        return metadata

    def get_model_provenance(self) -> dict[str, object]:
        if self._model_provenance_cache is not None:
            return dict(self._model_provenance_cache)
        path = Path(self.model_path)
        metadata: dict[str, object] = dict(self.get_backend_metadata())
        metadata.update(
            {
                "file_size_bytes": None,
                "modified_time_ns": None,
                "sha256": None,
            }
        )
        try:
            stat = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as model_file:
                for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
                    digest.update(chunk)
            metadata.update(
                {
                    "file_size_bytes": int(stat.st_size),
                    "modified_time_ns": int(stat.st_mtime_ns),
                    "sha256": digest.hexdigest(),
                }
            )
        except OSError:
            pass
        self._model_provenance_cache = metadata
        return dict(metadata)

    def get_inference_provenance(self) -> dict[str, object]:
        actual_device = None
        if self._model is not None:
            actual_device = getattr(self._model, "device", None)
            if actual_device is None:
                actual_device = getattr(getattr(self._model, "model", None), "device", None)
        return {
            "quality_settings": self.quality_config.to_summary(),
            "requested_device": self.quality_config.device,
            "actual_device": str(actual_device) if actual_device is not None else None,
            "gpu_acceleration": bool(
                self.quality_config.device not in (None, "cpu")
                or (actual_device is not None and "cuda" in str(actual_device).casefold())
            ),
            "mask_measurement_version": MASK_MEASUREMENT_VERSION,
            "crack_geometry_version": CRACK_GEOMETRY_VERSION,
            "severity_rule_id": SEVERITY_RULE_ID,
            "severity_rule_version": SEVERITY_RULE_VERSION,
        }

    def _result_metadata(self) -> dict[str, object]:
        return {
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "model_provenance": self.get_model_provenance(),
            "inference_provenance": self.get_inference_provenance(),
        }

    def _class_ids(self, result: object, count: int) -> np.ndarray:
        raw_classes = getattr(getattr(result, "boxes", None), "cls", None)
        if raw_classes is None:
            return np.zeros(count, dtype=np.int64)
        classes = _to_numpy_array(raw_classes).reshape(-1)
        if len(classes) < count:
            padded = np.zeros(count, dtype=np.int64)
            padded[: len(classes)] = classes.astype(np.int64, copy=False)
            return padded
        return classes[:count].astype(np.int64, copy=False)

    def _enriched_instance(
        self,
        *,
        result: object,
        model: object,
        instance_index: int,
        class_id: int,
        mask: np.ndarray,
        mask_scores: np.ndarray,
        box: Sequence[float],
        score: float,
        source_image_shape: Sequence[int],
    ) -> dict[str, object]:
        class_name = _resolve_class_name(
            class_id, getattr(result, "names", None), getattr(model, "names", None)
        )
        finding = _build_damage_finding(
            instance_index=instance_index,
            class_id=class_id,
            class_name=class_name,
            score=score,
            box=box,
            mask=mask,
            source_image_shape=source_image_shape,
        )
        return {**finding, "mask": mask, "mask_scores": mask_scores}

    def _render_result(
        self, image: np.ndarray, inference_result: dict[str, object]
    ) -> np.ndarray:
        import cv2

        rendered = np.asarray(image, dtype=np.uint8).copy()
        selected_mask = inference_result["selected_mask"]
        selected_mask_scores = inference_result.get("selected_mask_scores")
        selected_box = inference_result["selected_box"]
        score = inference_result["score"]
        inspection_instances = inference_result.get("inspection_instances")

        if inference_result["success"] and inspection_instances:
            palette = (
                (0.0, 255.0, 0.0),
                (255.0, 180.0, 0.0),
                (0.0, 180.0, 255.0),
                (255.0, 0.0, 180.0),
            )
            boxes_and_labels: list[tuple[list[float], str, tuple[int, int, int]]] = []
            for instance_index, instance in enumerate(inspection_instances):
                mask = np.asarray(instance["mask"], dtype=bool)
                alpha_mask = (
                    cv2.GaussianBlur(mask.astype(np.float32), (3, 3), 0)
                    if self.quality_config.high_quality_rendering
                    else mask.astype(np.float32)
                )
                alpha = (np.clip(alpha_mask, 0.0, 1.0) * 0.35)[..., None]
                color = palette[int(instance["class_id"]) % len(palette)]
                rendered = np.clip(
                    rendered.astype(np.float32) * (1.0 - alpha)
                    + np.asarray(color, dtype=np.float32) * alpha,
                    0.0,
                    255.0,
                ).astype(np.uint8)
                label = f"{instance['class_name']} {float(instance['score']):.3f}"
                boxes_and_labels.append((list(instance["box"]), label, tuple(int(value) for value in color)))

            for box, label, color in boxes_and_labels:
                x1, y1, x2, y2 = [int(round(value)) for value in box]
                cv2.rectangle(rendered, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    rendered,
                    label,
                    (max(0, x1), max(18, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                    cv2.LINE_AA,
                )
            return rendered

        if inference_result["success"] and selected_mask is not None:
            mask = np.asarray(selected_mask, dtype=bool)
            if (
                self.quality_config.high_quality_rendering
                and selected_mask_scores is not None
            ):
                alpha_mask = cv2.GaussianBlur(mask.astype(np.float32), (3, 3), 0)
                alpha_mask = np.clip(alpha_mask, 0.0, 1.0)
            else:
                alpha_mask = mask.astype(np.float32)

            alpha = (alpha_mask * 0.35)[..., None]
            overlay_color = np.array([0.0, 255.0, 0.0], dtype=np.float32)
            rendered = np.clip(
                rendered.astype(np.float32) * (1.0 - alpha) + overlay_color * alpha,
                0.0,
                255.0,
            ).astype(np.uint8)

            if selected_box is not None:
                x1, y1, x2, y2 = [int(round(value)) for value in selected_box]
                cv2.rectangle(rendered, (x1, y1), (x2, y2), (0, 0, 255), 2)

            label = f"score={float(score):.3f}" if score is not None else "detected"
            cv2.putText(
                rendered, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2
            )
            return rendered

        cv2.putText(
            rendered,
            "no detection",
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
        )
        return rendered

    def infer_instances(self, image: np.ndarray) -> dict[str, object]:
        model = self._ensure_model()
        start = time.perf_counter()
        results = model(image, **self.quality_config.model_kwargs())
        latency_ms = (time.perf_counter() - start) * 1000.0

        result = results[0] if results else None
        if (
            result is None
            or result.masks is None
            or getattr(result.boxes, "conf", None) is None
        ):
            return {
                "success": False,
                "instances": [],
                "latency_ms": float(latency_ms),
                **self._result_metadata(),
            }

        masks = _to_numpy_array(result.masks.data)
        boxes = _to_numpy_array(result.boxes.xyxy)
        scores = _to_numpy_array(result.boxes.conf)
        if masks.size == 0 or boxes.size == 0 or scores.size == 0:
            return {
                "success": False,
                "instances": [],
                "latency_ms": float(latency_ms),
                **self._result_metadata(),
            }

        instances: list[dict[str, object]] = []
        instance_count = min(len(masks), len(boxes), len(scores))
        class_ids = self._class_ids(result, instance_count)
        for instance_index in range(instance_count):
            selected_mask, selected_mask_scores = _resize_mask_to_image_shape(
                masks[instance_index],
                image,
                mask_threshold=self.quality_config.mask_threshold,
                mask_smoothing=self.quality_config.mask_smoothing,
                keep_largest_component=self.quality_config.keep_largest_component,
                fill_mask_holes=self.quality_config.fill_mask_holes,
            )
            instances.append(
                self._enriched_instance(
                    result=result,
                    model=model,
                    instance_index=instance_index,
                    class_id=int(class_ids[instance_index]),
                    mask=selected_mask,
                    mask_scores=selected_mask_scores,
                    box=boxes[instance_index].tolist(),
                    score=float(scores[instance_index]),
                    source_image_shape=image.shape,
                )
            )

        return {
            "success": bool(instances),
            "instances": instances,
            "latency_ms": float(latency_ms),
            **self._result_metadata(),
        }

    def infer_instances_low_resolution(self, image: np.ndarray) -> dict[str, object]:
        model = self._ensure_model()
        start = time.perf_counter()
        results = model(image, **self.quality_config.model_kwargs())
        latency_ms = (time.perf_counter() - start) * 1000.0

        result = results[0] if results else None
        if (
            result is None
            or result.masks is None
            or getattr(result.boxes, "conf", None) is None
        ):
            return {
                "success": False,
                "instances": [],
                "latency_ms": float(latency_ms),
                **self._result_metadata(),
            }

        masks = _to_numpy_array(result.masks.data).astype(np.float32, copy=False)
        boxes = _to_numpy_array(result.boxes.xyxy)
        scores = _to_numpy_array(result.boxes.conf)
        if masks.size == 0 or boxes.size == 0 or scores.size == 0:
            return {
                "success": False,
                "instances": [],
                "latency_ms": float(latency_ms),
                **self._result_metadata(),
            }

        instances: list[dict[str, object]] = []
        instance_count = min(len(masks), len(boxes), len(scores))
        class_ids = self._class_ids(result, instance_count)
        for instance_index in range(instance_count):
            mask_scores = np.asarray(masks[instance_index], dtype=np.float32)
            np.clip(mask_scores, 0.0, 1.0, out=mask_scores)
            selected_mask = mask_scores >= self.quality_config.mask_threshold
            instances.append(
                self._enriched_instance(
                    result=result,
                    model=model,
                    instance_index=instance_index,
                    class_id=int(class_ids[instance_index]),
                    mask=selected_mask,
                    mask_scores=mask_scores,
                    box=boxes[instance_index].tolist(),
                    score=float(scores[instance_index]),
                    source_image_shape=image.shape,
                )
            )

        return {
            "success": bool(instances),
            "instances": instances,
            "latency_ms": float(latency_ms),
            **self._result_metadata(),
        }

    def infer_low_resolution(self, image: np.ndarray) -> dict[str, object]:
        result = self.infer_instances_low_resolution(image)
        instances = result["instances"]
        if not instances:
            return {
                "success": False,
                "instances": [],
                "selected_mask": None,
                "selected_mask_scores": None,
                "selected_box": None,
                "score": None,
                "latency_ms": float(result["latency_ms"]),
                "damage_finding": None,
                "damage_findings": [],
                **self._result_metadata(),
            }
        selected_instance = max(
            instances, key=lambda instance: float(instance["score"])
        )
        return {
            "success": True,
            "selected_mask": selected_instance["mask"],
            "selected_mask_scores": selected_instance["mask_scores"],
            "selected_box": selected_instance["box"],
            "score": selected_instance["score"],
            "latency_ms": float(result["latency_ms"]),
            "damage_finding": _json_safe_finding(selected_instance),
            "damage_findings": [
                _json_safe_finding(instance) for instance in instances
            ],
            **self._result_metadata(),
        }

    def infer(self, image: np.ndarray) -> dict[str, object]:
        result = self.infer_instances(image)
        instances = result["instances"]
        if not instances:
            return {
                "success": False,
                "selected_mask": None,
                "selected_mask_scores": None,
                "selected_box": None,
                "score": None,
                "latency_ms": float(result["latency_ms"]),
                "damage_finding": None,
                "damage_findings": [],
                **self._result_metadata(),
            }

        selected_instance = max(instances, key=lambda instance: float(instance["score"]))
        selected_mask = np.asarray(selected_instance["mask"], dtype=bool)
        selected_mask_scores = np.asarray(
            selected_instance["mask_scores"], dtype=np.float32
        )
        final_finding = _build_damage_finding(
            instance_index=int(selected_instance["index"]),
            class_id=int(selected_instance["class_id"]),
            class_name=str(selected_instance["class_name"]),
            score=float(selected_instance["score"]),
            box=selected_instance["box"],
            mask=selected_mask,
            source_image_shape=image.shape,
        )
        all_findings = [
            final_finding
            if int(instance["index"]) == int(selected_instance["index"])
            else _json_safe_finding(instance)
            for instance in instances
        ]
        return {
            "success": True,
            "instances": instances,
            "selected_mask": selected_mask,
            "selected_mask_scores": selected_mask_scores,
            "selected_box": selected_instance["box"],
            "score": selected_instance["score"],
            "latency_ms": float(result["latency_ms"]),
            "damage_finding": final_finding,
            "damage_findings": all_findings,
            **self._result_metadata(),
        }

    def infer_inspection(self, image: np.ndarray) -> dict[str, object]:
        result = self.infer(image)
        inspection_instances = aggregate_inspection_instances(
            result.get("instances", []),
            merge_iou=self.inspection_merge_iou,
        )
        return {
            **result,
            "inspection_instances": inspection_instances,
            "damage_findings": [
                _json_safe_finding(instance) for instance in inspection_instances
            ],
        }

    def process_path(
        self,
        input_path: str | Path,
        *,
        output_dir: str | Path | None = None,
        image_reader: ImageReader | None = None,
        image_writer: ImageWriter | None = None,
        progress: bool = False,
        progress_writer: ProgressWriter | None = None,
    ) -> dict[str, object]:
        resolved_input = Path(input_path)
        image_paths = _resolve_image_paths(resolved_input)
        resolved_output_dir = (
            Path(output_dir)
            if output_dir is not None
            else _default_output_dir_for_input(resolved_input)
        )
        resolved_output_dir.mkdir(parents=True, exist_ok=True)

        reader = image_reader or self._default_image_reader
        writer = image_writer or self._default_image_writer
        progress_sink = progress_writer or (lambda message: print(message, flush=True))

        def emit_progress(message: str) -> None:
            if progress:
                progress_sink(message)

        results_summary: list[dict[str, object]] = []
        success_count = 0
        emit_progress(
            f"Starting YOLO segmentation: {len(image_paths)} image(s), "
            f"model={self.model_path}, output={resolved_output_dir}"
        )

        for image_index, image_path in enumerate(image_paths, start=1):
            emit_progress(
                f"[{image_index}/{len(image_paths)}] Reading {image_path.name}"
            )
            image = reader(str(image_path))
            if image is None:
                raise ValueError(f"Failed to read image: {image_path}")

            if self._model is None:
                emit_progress(
                    "Loading YOLO model and backend; first inference can take several seconds..."
                )
            inference_result = self.infer_inspection(image)
            inspection_instances = inference_result["inspection_instances"]
            rendered = self._render_result(image, inference_result)
            output_image_path = resolved_output_dir / image_path.name
            if writer(str(output_image_path), rendered) is False:
                raise IOError(f"Failed to write inference image: {output_image_path}")
            emit_progress(
                f"[{image_index}/{len(image_paths)}] Wrote {output_image_path} "
                f"success={bool(inference_result['success'])} latency_ms={float(inference_result['latency_ms']):.2f}"
            )

            if inference_result["success"]:
                success_count += 1

            results_summary.append(
                {
                    "image_name": image_path.name,
                    "image_path": str(image_path),
                    "output_path": str(output_image_path),
                    "success": bool(inference_result["success"]),
                    "score": inference_result["score"],
                    "selected_box": inference_result["selected_box"],
                    "damage_finding": inference_result["damage_finding"],
                    "damage_findings": inference_result["damage_findings"],
                    "raw_instance_count": len(inference_result.get("instances", [])),
                    "inspection_finding_count": len(inspection_instances),
                    "latency_ms": round(float(inference_result["latency_ms"]), 6),
                }
            )

        summary = {
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "model_path": self.model_path,
            "model_artifact_suffix": self.get_backend_metadata()["artifact_suffix"],
            "backend_metadata": self.get_backend_metadata(),
            "model_provenance": self.get_model_provenance(),
            "inference_provenance": self.get_inference_provenance(),
            "input_path": str(resolved_input),
            "output_dir": str(resolved_output_dir),
            "quality_settings": self.quality_config.to_summary(),
            "inspection_settings": {
                "mode": "full",
                "merge_same_class_masks": True,
                "merge_iou": self.inspection_merge_iou,
            },
            "processed_count": len(results_summary),
            "success_count": success_count,
            "results": results_summary,
        }
        summary_path = resolved_output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        emit_progress(f"Summary written to {summary_path}")
        return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用YOLO11s-seg对单张图像或图像目录执行损伤分割推理。",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示本帮助信息并退出。")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help=f"输入图像或图像目录的路径。默认值：{DEFAULT_INPUT_PATH}。",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help=f"可选输出目录。未指定时在 {DEFAULT_OUTPUT_ROOT} 下按输入名称创建目录。",
    )
    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL_PATH),
        help=f"YOLO11s-seg模型文件路径。默认值：{DEFAULT_MODEL_PATH}。",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
        help=f"推理设备编号，例如0或cpu。默认值：{DEFAULT_DEVICE}。",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=DEFAULT_INFERENCE_IMGSZ,
        help=f"YOLO推理图像尺寸。默认值：{DEFAULT_INFERENCE_IMGSZ}，用于保留更清晰的掩膜边缘。",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"YOLO检测置信度阈值。默认值：{DEFAULT_CONFIDENCE_THRESHOLD}。",
    )
    parser.add_argument(
        "--iou",
        type=float,
        default=DEFAULT_IOU_THRESHOLD,
        help=f"YOLO非极大值抑制交并比阈值。默认值：{DEFAULT_IOU_THRESHOLD}。",
    )
    parser.add_argument(
        "--retina-masks",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_RETINA_MASKS,
        help="在模型支持时请求原图分辨率掩膜。默认启用。",
    )
    parser.add_argument(
        "--mask-threshold",
        type=float,
        default=DEFAULT_MASK_THRESHOLD,
        help=f"掩膜缩放和平滑后的二值化阈值。默认值：{DEFAULT_MASK_THRESHOLD}。",
    )
    parser.add_argument(
        "--mask-smoothing",
        type=int,
        default=DEFAULT_MASK_SMOOTHING,
        help=f"保守掩膜平滑的形态学核尺寸。默认值：{DEFAULT_MASK_SMOOTHING}，零表示关闭。",
    )
    parser.add_argument(
        "--keep-largest-component",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_KEEP_LARGEST_COMPONENT,
        help="仅保留最大掩膜连通区域，以移除孤立误检区域。默认启用。",
    )
    parser.add_argument(
        "--fill-mask-holes",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_FILL_MASK_HOLES,
        help="填充所选掩膜内部的封闭孔洞。默认启用。",
    )
    parser.add_argument(
        "--high-quality-rendering",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_HIGH_QUALITY_RENDERING,
        help="渲染带抗锯齿效果的掩膜预览边缘。默认启用。",
    )
    parser.add_argument(
        "--inspection-merge-iou",
        type=float,
        default=DEFAULT_INSPECTION_MERGE_IOU,
        help=(
            "全量巡检中同类别掩膜的最小合并交并比。"
            f"默认值：{DEFAULT_INSPECTION_MERGE_IOU}。"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=DEFAULT_QUIET,
        help="隐藏推理进度信息，仅输出最终JSON摘要。",
    )
    return parser


def main(argv: list[str] | None = None) -> dict[str, object]:
    args = build_parser().parse_args(argv)
    model_path = resolve_project_path(args.model)
    input_path = resolve_project_path(args.input)
    output_path = resolve_project_path(args.output)
    assert model_path is not None
    assert input_path is not None
    runtime = YoloSegmentationRuntime(
        model_path=model_path,
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.iou,
        device=args.device,
        retina_masks=args.retina_masks,
        mask_threshold=args.mask_threshold,
        mask_smoothing=args.mask_smoothing,
        keep_largest_component=args.keep_largest_component,
        fill_mask_holes=args.fill_mask_holes,
        high_quality_rendering=args.high_quality_rendering,
        inspection_merge_iou=args.inspection_merge_iou,
    )
    summary = runtime.process_path(
        input_path, output_dir=output_path, progress=not args.quiet
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    main()
