from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np


class _FakeBoxes:
    def __init__(self, xyxy, conf, cls=None):
        self.xyxy = np.asarray(xyxy, dtype=np.float32)
        self.conf = np.asarray(conf, dtype=np.float32)
        self.cls = (
            np.asarray(cls, dtype=np.float32)
            if cls is not None
            else np.zeros(len(self.conf), dtype=np.float32)
        )


class _FakeMasks:
    def __init__(self, data):
        self.data = np.asarray(data, dtype=np.float32)


class _FakeResult:
    def __init__(self, masks, boxes, names=None):
        self.masks = masks
        self.boxes = boxes
        self.names = names


class _FakeCudaTensor:
    def __init__(self, data):
        self._data = np.asarray(data)

    def cpu(self):
        return self._data

    def __array__(self, dtype=None):
        raise TypeError("can't convert cuda tensor directly")


def test_runtime_loads_model_lazily() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    calls: list[str] = []

    def fake_loader(model_path: str):
        calls.append(model_path)

        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]), _FakeBoxes([[1, 2, 3, 4]], [0.9])
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )

    assert calls == []
    runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))
    assert calls == ["best.pt"]

    runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))
    assert calls == ["best.pt"]


def test_runtime_accepts_engine_model_path() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    calls: list[str] = []

    def fake_loader(model_path: str):
        calls.append(model_path)

        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]), _FakeBoxes([[1, 2, 3, 4]], [0.9])
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.engine", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))

    assert calls == ["best.engine"]
    assert result["success"] is True
    assert runtime.get_backend_metadata()["backend"] == "tensorrt"


def test_runtime_enriches_damage_instance_with_class_area_and_severity() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    mask = np.zeros((10, 10), dtype=np.float32)
    mask[2:4, 3:6] = 1.0

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([mask]),
                    _FakeBoxes([[3, 2, 6, 4]], [0.87], [5]),
                    names={5: "Rebar corrosion"},
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="missing.pt",
        model_loader=fake_loader,
        mask_smoothing=0,
        keep_largest_component=False,
        fill_mask_holes=False,
    )
    result = runtime.infer_instances(np.zeros((10, 10, 3), dtype=np.uint8))
    instance = result["instances"][0]

    assert instance["class_id"] == 5
    assert instance["class_name"] == "Rebar corrosion"
    assert instance["detection_confidence"] == 0.87
    assert instance["area"]["area_pixels"] == 6
    assert instance["area"]["area_ratio"] == 0.06
    assert instance["screening_severity"]["level"] == "high"
    assert instance["crack_geometry"]["applicable"] is False
    assert result["result_schema_version"] == "damage-finding.v1"


def test_runtime_measures_crack_geometry_without_claiming_physical_units() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    mask = np.zeros((20, 20), dtype=np.float32)
    mask[8:11, 2:18] = 1.0

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([mask]),
                    _FakeBoxes([[2, 8, 18, 11]], [0.95], [6]),
                    names={6: "Structural crack"},
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="missing.pt",
        model_loader=fake_loader,
        mask_smoothing=0,
        keep_largest_component=False,
        fill_mask_holes=False,
    )
    geometry = runtime.infer_instances(
        np.zeros((20, 20, 3), dtype=np.uint8)
    )["instances"][0]["crack_geometry"]

    assert geometry["applicable"] is True
    assert geometry["length_px"] > 10.0
    assert 2.0 <= geometry["mean_width_px"] <= 4.0
    assert geometry["maximum_width_px"] >= 2.0
    assert geometry["physical_length"] is None
    assert geometry["physical_unit"] is None


def test_screening_severity_thresholds_are_deterministic() -> None:
    from runtime.yolo_segmentation_runtime import _screening_severity

    assert _screening_severity(0.0099, 1)["level"] == "low"
    assert _screening_severity(0.01, 1)["level"] == "medium"
    assert _screening_severity(0.0499, 1)["level"] == "medium"
    assert _screening_severity(0.05, 1)["level"] == "high"
    assert _screening_severity(0.0, 0)["level"] == "undetermined"


def test_model_provenance_hashes_readable_artifact_and_handles_missing(tmp_path) -> None:
    import hashlib
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    artifact = tmp_path / "best.pt"
    artifact.write_bytes(b"model-artifact")
    runtime = YoloSegmentationRuntime(
        model_path=artifact, model_loader=lambda _path: None
    )
    provenance = runtime.get_model_provenance()

    assert provenance["file_size_bytes"] == len(b"model-artifact")
    assert provenance["sha256"] == hashlib.sha256(b"model-artifact").hexdigest()
    assert provenance["modified_time_ns"] is not None

    missing = YoloSegmentationRuntime(
        model_path=tmp_path / "missing.engine", model_loader=lambda _path: None
    ).get_model_provenance()
    assert missing["backend"] == "tensorrt"
    assert missing["file_size_bytes"] is None
    assert missing["sha256"] is None


def test_runtime_uses_class_fallback_when_names_are_unavailable() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]),
                    _FakeBoxes([[0, 0, 1, 1]], [0.9], [7]),
                )
            ]

        return fake_model

    instance = YoloSegmentationRuntime(
        model_path="missing.pt", model_loader=fake_loader, mask_smoothing=0
    ).infer_instances(np.zeros((2, 2, 3), dtype=np.uint8))["instances"][0]

    assert instance["class_id"] == 7
    assert instance["class_name"] == "class_7"


def test_runtime_returns_empty_result_when_no_instance_is_detected() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=None, boxes=_FakeBoxes([], []))]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((8, 8, 3), dtype=np.uint8))

    assert result["success"] is False
    assert result["selected_mask"] is None
    assert result["selected_box"] is None
    assert result["score"] is None
    assert isinstance(result["latency_ms"], float)


def test_runtime_selects_single_target_instance() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    masks = _FakeMasks(
        [
            [[0, 1], [0, 1]],
            [[1, 1], [1, 0]],
        ]
    )
    boxes = _FakeBoxes(
        [
            [10, 10, 20, 20],
            [30, 30, 50, 60],
        ],
        [0.55, 0.91],
    )

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))

    assert result["success"] is True
    assert result["score"] == 0.91
    assert result["selected_box"] == [30.0, 30.0, 50.0, 60.0]
    assert result["selected_mask"].dtype == np.bool_
    assert result["selected_mask"].tolist() == [[True, True], [True, False]]


def _inspection_instance(mask: list[list[bool]], class_id: int, score: float, index: int) -> dict[str, object]:
    array = np.asarray(mask, dtype=bool)
    ys, xs = np.where(array)
    return {
        "index": index,
        "class_id": class_id,
        "class_name": f"class_{class_id}",
        "score": score,
        "box": [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)],
        "mask": array,
        "mask_scores": array.astype(np.float32),
    }


def test_aggregate_inspection_instances_merges_overlapping_same_class_masks() -> None:
    from runtime.yolo_segmentation_runtime import aggregate_inspection_instances

    instances = [
        _inspection_instance([[1, 1, 0], [1, 0, 0], [0, 0, 0]], 6, 0.80, 0),
        _inspection_instance([[0, 1, 1], [0, 0, 1], [0, 0, 0]], 6, 0.90, 1),
    ]

    aggregated = aggregate_inspection_instances(instances, merge_iou=0.1)

    assert len(aggregated) == 1
    assert aggregated[0]["class_id"] == 6
    assert aggregated[0]["score"] == 0.90
    assert aggregated[0]["member_indices"] == [0, 1]
    assert aggregated[0]["mask"].tolist() == [
        [True, True, True],
        [True, False, True],
        [False, False, False],
    ]
    assert aggregated[0]["box"] == [0.0, 0.0, 3.0, 2.0]


def test_aggregate_inspection_instances_keeps_different_or_disjoint_masks_separate() -> None:
    from runtime.yolo_segmentation_runtime import aggregate_inspection_instances

    instances = [
        _inspection_instance([[1, 1, 0], [0, 0, 0], [0, 0, 0]], 6, 0.80, 0),
        _inspection_instance([[0, 1, 0], [0, 0, 0], [0, 0, 0]], 5, 0.90, 1),
        _inspection_instance([[0, 0, 0], [0, 0, 0], [0, 0, 1]], 6, 0.70, 2),
    ]

    aggregated = aggregate_inspection_instances(instances, merge_iou=0.1)

    assert len(aggregated) == 3
    assert [item["member_indices"] for item in aggregated] == [[0], [1], [2]]


def test_infer_inspection_aggregates_findings_and_preserves_selected_target() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    masks = _FakeMasks(
        [
            [[1, 1, 0], [1, 0, 0], [0, 0, 0]],
            [[0, 1, 1], [0, 0, 1], [0, 0, 0]],
        ]
    )
    boxes = _FakeBoxes([[0, 0, 2, 2], [1, 0, 3, 2]], [0.80, 0.90], [6, 6])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes, names={6: "Structural crack"})]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt",
        model_loader=fake_loader,
        mask_smoothing=0,
        keep_largest_component=False,
        fill_mask_holes=False,
        inspection_merge_iou=0.1,
    )
    result = runtime.infer_inspection(np.zeros((3, 3, 3), dtype=np.uint8))

    assert result["selected_box"] == [1.0, 0.0, 3.0, 2.0]
    assert len(result["instances"]) == 2
    assert len(result["inspection_instances"]) == 1
    assert len(result["damage_findings"]) == 1
    assert result["damage_findings"][0]["member_indices"] == [0, 1]
    assert result["damage_findings"][0]["merged_instance_count"] == 2


def test_process_path_renders_all_full_inspection_findings(tmp_path) -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    input_dir = tmp_path / "images"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    (input_dir / "a.jpg").write_text("placeholder", encoding="utf-8")
    written: list[np.ndarray] = []

    def fake_reader(_image_path: str) -> np.ndarray:
        return np.zeros((4, 4, 3), dtype=np.uint8)

    def fake_writer(_image_path: str, image: np.ndarray) -> bool:
        written.append(image)
        return True

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks(
                        [
                            [[1, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]],
                            [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0]],
                        ]
                    ),
                    _FakeBoxes([[0, 0, 1, 1], [2, 2, 3, 3]], [0.8, 0.7], [0, 1]),
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(model_loader=fake_loader, mask_smoothing=0)
    summary = runtime.process_path(
        input_dir,
        output_dir=output_dir,
        image_reader=fake_reader,
        image_writer=fake_writer,
    )

    assert len(summary["results"][0]["damage_findings"]) == 2
    assert written[0][0, 0].any()
    assert written[0][2, 2].any()


def test_runtime_uses_only_formal_model_by_default() -> None:
    from runtime.yolo_segmentation_runtime import (
        DEFAULT_INPUT_PATH,
        DEFAULT_MODEL_PATH,
        YoloSegmentationRuntime,
    )

    runtime = YoloSegmentationRuntime(model_loader=lambda _model_path: None)

    project_root = Path(__file__).resolve().parents[1]
    workspace_root = project_root.parent
    assert DEFAULT_MODEL_PATH.is_absolute()
    expected_formal_model = (project_root / "models/best.pt").resolve()
    assert DEFAULT_MODEL_PATH == expected_formal_model
    assert DEFAULT_MODEL_PATH.is_file()
    assert runtime.model_path == str(DEFAULT_MODEL_PATH)
    assert DEFAULT_INPUT_PATH == (workspace_root / "dataset_yolo11_seg/images/test").resolve()


def test_runtime_provenance_records_requested_gpu_device() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    runtime = YoloSegmentationRuntime(model_loader=lambda _model_path: None, device="0")
    provenance = runtime.get_inference_provenance()
    assert provenance["requested_device"] == "0"
    assert provenance["gpu_acceleration"] is True


def test_runtime_processes_image_directory_and_writes_outputs(tmp_path) -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    input_dir = tmp_path / "images"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    (input_dir / "b.PNG").write_text("placeholder", encoding="utf-8")
    (input_dir / "a.jpg").write_text("placeholder", encoding="utf-8")
    (input_dir / "ignore.txt").write_text("placeholder", encoding="utf-8")

    read_calls: list[str] = []
    write_calls: list[str] = []

    def fake_reader(image_path: str):
        read_calls.append(Path(image_path).name)
        return np.zeros((2, 2, 3), dtype=np.uint8)

    def fake_writer(image_path: str, _image: np.ndarray) -> bool:
        write_calls.append(Path(image_path).name)
        return True

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]), _FakeBoxes([[1, 2, 3, 4]], [0.9])
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(model_loader=fake_loader)
    summary = runtime.process_path(
        input_dir,
        output_dir=output_dir,
        image_reader=fake_reader,
        image_writer=fake_writer,
    )

    assert read_calls == ["a.jpg", "b.PNG"]
    assert write_calls == ["a.jpg", "b.PNG"]
    assert summary["processed_count"] == 2
    assert summary["success_count"] == 2
    assert [item["image_name"] for item in summary["results"]] == ["a.jpg", "b.PNG"]

    summary_path = output_dir / "summary.json"
    assert summary_path.exists()
    saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert saved_summary["result_schema_version"] == "damage-finding.v1"
    assert saved_summary["model_provenance"]["sha256"] is not None
    assert saved_summary["inference_provenance"]["severity_rule_version"] == "1.0.0"
    assert saved_summary["results"][0]["damage_finding"]["class_id"] == 0
    assert saved_summary["results"][0]["damage_finding"]["area"]["area_pixels"] == 1
    assert len(saved_summary["results"][0]["damage_findings"]) == 1
    assert saved_summary["model_path"] == runtime.model_path
    assert saved_summary["quality_settings"]["retina_masks"] is True
    assert saved_summary["quality_settings"]["imgsz"] == 1280
    assert saved_summary["quality_settings"]["conf"] == 0.25
    assert saved_summary["quality_settings"]["iou"] == 0.7
    assert saved_summary["quality_settings"]["mask_threshold"] == 0.5
    assert saved_summary["quality_settings"]["mask_smoothing"] == 0
    assert saved_summary["quality_settings"]["keep_largest_component"] is True
    assert saved_summary["quality_settings"]["fill_mask_holes"] is True
    assert saved_summary["processed_count"] == 2


def test_default_image_reader_and_writer_support_unicode_windows_paths(tmp_path) -> None:
    import cv2

    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    unicode_dir = tmp_path / "中文路径" / "测试图像"
    unicode_dir.mkdir(parents=True)
    input_path = unicode_dir / "腐蚀裂纹.jpg"
    output_path = unicode_dir / "输出结果.jpg"
    source = np.zeros((8, 9, 3), dtype=np.uint8)
    source[:, :, 1] = 127
    encoded_ok, encoded = cv2.imencode(".jpg", source)
    assert encoded_ok is True
    input_path.write_bytes(encoded.tobytes())

    runtime = YoloSegmentationRuntime(model_loader=lambda _model_path: None)
    decoded = runtime._default_image_reader(str(input_path))
    assert decoded is not None
    assert decoded.shape == source.shape
    assert runtime._default_image_writer(str(output_path), decoded) is True
    assert output_path.is_file()
    assert cv2.imdecode(
        np.fromfile(output_path, dtype=np.uint8), cv2.IMREAD_COLOR
    ) is not None


def test_runtime_summary_exposes_backend_metadata(tmp_path) -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    input_dir = tmp_path / "images"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    (input_dir / "a.jpg").write_text("placeholder", encoding="utf-8")

    def fake_reader(_image_path: str):
        return np.zeros((2, 2, 3), dtype=np.uint8)

    def fake_writer(_image_path: str, _image: np.ndarray) -> bool:
        return True

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]), _FakeBoxes([[1, 2, 3, 4]], [0.9])
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="deployed.engine", model_loader=fake_loader, mask_smoothing=0
    )
    summary = runtime.process_path(
        input_dir,
        output_dir=output_dir,
        image_reader=fake_reader,
        image_writer=fake_writer,
    )

    assert summary["model_artifact_suffix"] == ".engine"
    assert summary["backend_metadata"]["backend"] == "tensorrt"
    assert summary["backend_metadata"]["model_filename"] == "deployed.engine"

    summary_path = output_dir / "summary.json"
    saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert saved_summary["backend_metadata"]["backend"] == "tensorrt"


def test_runtime_converts_tensor_outputs_to_numpy_before_selection() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    masks = type(
        "FakeMasks", (), {"data": _FakeCudaTensor([[[0, 1], [1, 1]], [[1, 0], [0, 0]]])}
    )()
    boxes = type(
        "FakeBoxes",
        (),
        {
            "xyxy": _FakeCudaTensor([[10, 10, 20, 20], [30, 30, 50, 60]]),
            "conf": _FakeCudaTensor([0.55, 0.91]),
        },
    )()

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))

    assert result["success"] is True
    assert result["score"] == 0.91
    assert result["selected_box"] == [30.0, 30.0, 50.0, 60.0]
    assert result["selected_mask"].tolist() == [[True, False], [False, False]]


def test_runtime_resizes_selected_mask_to_input_image_shape() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    masks = _FakeMasks(
        [
            [[0, 1], [1, 0]],
        ]
    )
    boxes = _FakeBoxes(
        [
            [10, 10, 20, 20],
        ],
        [0.95],
    )

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((4, 6, 3), dtype=np.uint8))

    assert result["success"] is True
    assert result["selected_mask"].shape == (4, 6)


def test_runtime_smoothly_resizes_mask_scores_before_thresholding() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    masks = _FakeMasks(
        [
            [[0, 1], [0, 1]],
        ]
    )
    boxes = _FakeBoxes([[0, 0, 4, 2]], [0.95])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((2, 4, 3), dtype=np.uint8))

    assert result["success"] is True
    assert result["selected_mask"].tolist() == [
        [False, False, True, True],
        [False, False, True, True],
    ]
    assert 0.0 < float(result["selected_mask_scores"][0, 1]) < 1.0
    assert 0.0 < float(result["selected_mask_scores"][0, 2]) < 1.0


def test_runtime_applies_mask_threshold_after_smooth_resize() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    masks = _FakeMasks(
        [
            [[0, 1], [0, 1]],
        ]
    )
    boxes = _FakeBoxes([[0, 0, 3, 2]], [0.95])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt",
        model_loader=fake_loader,
        mask_threshold=0.75,
        mask_smoothing=0,
    )
    result = runtime.infer(np.zeros((2, 3, 3), dtype=np.uint8))

    assert result["success"] is True
    assert result["selected_mask"].tolist() == [
        [False, False, True],
        [False, False, True],
    ]


def test_runtime_removes_disconnected_mask_islands_by_default() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    masks = _FakeMasks(
        [
            [
                [0, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 0, 0],
                [0, 1, 1, 1, 0, 0],
                [0, 0, 0, 0, 0, 1],
            ],
        ]
    )
    boxes = _FakeBoxes([[0, 0, 6, 4]], [0.95])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((4, 6, 3), dtype=np.uint8))

    assert result["success"] is True
    assert result["selected_mask"].tolist() == [
        [False, False, False, False, False, False],
        [False, True, True, True, False, False],
        [False, True, True, True, False, False],
        [False, False, False, False, False, False],
    ]


def test_runtime_largest_component_does_not_use_connected_components(
    monkeypatch,
) -> None:
    import cv2
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    def fail_connected_components(*_args, **_kwargs):
        raise AssertionError(
            "connectedComponentsWithStats should not be used for largest-component cleanup"
        )

    monkeypatch.setattr(cv2, "connectedComponentsWithStats", fail_connected_components)
    masks = _FakeMasks(
        [
            [
                [0, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 0, 0],
                [0, 1, 1, 1, 0, 0],
                [0, 0, 0, 0, 0, 1],
            ],
        ]
    )
    boxes = _FakeBoxes([[0, 0, 6, 4]], [0.95])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((4, 6, 3), dtype=np.uint8))

    assert result["selected_mask"].sum() == 6


def test_runtime_fills_enclosed_mask_holes_by_default() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    masks = _FakeMasks(
        [
            [
                [0, 0, 0, 0, 0],
                [0, 1, 1, 1, 0],
                [0, 1, 0, 1, 0],
                [0, 1, 1, 1, 0],
                [0, 0, 0, 0, 0],
            ],
        ]
    )
    boxes = _FakeBoxes([[0, 0, 5, 5]], [0.95])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((5, 5, 3), dtype=np.uint8))

    assert result["success"] is True
    assert result["selected_mask"].tolist() == [
        [False, False, False, False, False],
        [False, True, True, True, False],
        [False, True, True, True, False],
        [False, True, True, True, False],
        [False, False, False, False, False],
    ]


def test_runtime_fill_holes_does_not_use_numpy_isin(monkeypatch) -> None:
    import numpy as numpy_module
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    def fail_isin(*_args, **_kwargs):
        raise AssertionError("np.isin should not be used for mask hole filling")

    monkeypatch.setattr(numpy_module, "isin", fail_isin)
    masks = _FakeMasks(
        [
            [
                [0, 0, 0, 0, 0],
                [0, 1, 1, 1, 0],
                [0, 1, 0, 1, 0],
                [0, 1, 1, 1, 0],
                [0, 0, 0, 0, 0],
            ],
        ]
    )
    boxes = _FakeBoxes([[0, 0, 5, 5]], [0.95])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=masks, boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((5, 5, 3), dtype=np.uint8))

    assert result["selected_mask"].tolist()[2][2] is True


def test_runtime_fill_holes_does_not_use_allocating_boolean_or(monkeypatch) -> None:
    import runtime.yolo_segmentation_runtime as module

    original_asarray = module.np.asarray

    class _NoOrArray(np.ndarray):
        def __or__(self, _other):
            raise AssertionError("hole filling should not allocate via boolean OR")

    def guarded_asarray(value, *args, **kwargs):
        result = original_asarray(value, *args, **kwargs)
        if result.dtype == np.bool_ and result.shape == (5, 5):
            return result.view(_NoOrArray)
        return result

    monkeypatch.setattr(module.np, "asarray", guarded_asarray)

    mask = np.array(
        [
            [0, 0, 0, 0, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 0, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 0, 0, 0, 0],
        ],
        dtype=bool,
    )

    filled = module._fill_enclosed_mask_holes(mask)

    assert filled[2, 2] is True or bool(filled[2, 2]) is True


def test_runtime_clips_mask_scores_in_place(monkeypatch) -> None:
    import runtime.yolo_segmentation_runtime as module
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    calls: list[dict[str, object]] = []
    original_clip = module.np.clip

    def recording_clip(a, a_min, a_max, out=None, **kwargs):
        calls.append({"same_out": out is a, "shape": getattr(a, "shape", None)})
        return original_clip(a, a_min, a_max, out=out, **kwargs)

    monkeypatch.setattr(module.np, "clip", recording_clip)

    mask = np.array([[[1.2, -0.5], [0.2, 0.8]]], dtype=np.float32)
    boxes = _FakeBoxes([[0, 0, 2, 2]], [0.95])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=_FakeMasks(mask), boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt",
        model_loader=fake_loader,
        mask_smoothing=0,
        keep_largest_component=False,
        fill_mask_holes=False,
    )
    runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))

    assert any(call["same_out"] is True for call in calls)


def test_runtime_low_resolution_instances_do_not_resize_masks(monkeypatch) -> None:
    import runtime.yolo_segmentation_runtime as module
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    def fail_full_resolution_resize(*_args, **_kwargs):
        raise AssertionError(
            "low-resolution instance extraction should not use full-resolution resize path"
        )

    monkeypatch.setattr(
        module, "_resize_mask_to_image_shape", fail_full_resolution_resize
    )
    mask = np.array([[[1.0, 0.0], [0.0, 1.0]]], dtype=np.float32)
    boxes = _FakeBoxes([[0, 0, 20, 20]], [0.95])

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [_FakeResult(masks=_FakeMasks(mask), boxes=boxes)]

        return fake_model

    runtime = YoloSegmentationRuntime(model_path="best.pt", model_loader=fake_loader)
    result = runtime.infer_instances_low_resolution(
        np.zeros((20, 20, 3), dtype=np.uint8)
    )

    assert result["instances"][0]["mask"].shape == (2, 2)


def test_runtime_passes_quality_options_to_model() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    recorded_kwargs: list[dict[str, object]] = []

    def fake_loader(_model_path: str):
        def fake_model(_image, **kwargs):
            recorded_kwargs.append(kwargs)
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]), _FakeBoxes([[1, 2, 3, 4]], [0.9])
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt",
        model_loader=fake_loader,
        imgsz=1280,
        conf=0.3,
        iou=0.7,
        retina_masks=False,
    )
    runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))

    assert recorded_kwargs == [
        {
            "verbose": False,
            "retina_masks": False,
            "imgsz": 1280,
                "conf": 0.3,
                "iou": 0.7,
                "device": "0",
            }
        ]


def test_runtime_progress_output_reports_startup_and_images(tmp_path) -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    input_dir = tmp_path / "images"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    (input_dir / "a.jpg").write_text("placeholder", encoding="utf-8")
    progress_messages: list[str] = []

    def fake_reader(_image_path: str):
        return np.zeros((2, 2, 3), dtype=np.uint8)

    def fake_writer(_image_path: str, _image: np.ndarray) -> bool:
        return True

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]), _FakeBoxes([[1, 2, 3, 4]], [0.9])
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    runtime.process_path(
        input_dir,
        output_dir=output_dir,
        image_reader=fake_reader,
        image_writer=fake_writer,
        progress=True,
        progress_writer=progress_messages.append,
    )

    assert progress_messages[0].startswith("Starting YOLO segmentation: 1 image(s)")
    assert any("Loading YOLO model" in message for message in progress_messages)
    assert any(
        "Wrote" in message and "latency_ms=" in message for message in progress_messages
    )


def test_runtime_passes_explicit_device_to_model() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    recorded_kwargs: list[dict[str, object]] = []

    def fake_loader(_model_path: str):
        def fake_model(_image, **kwargs):
            recorded_kwargs.append(dict(kwargs))
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]), _FakeBoxes([[1, 2, 3, 4]], [0.9])
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt",
        model_loader=fake_loader,
        mask_smoothing=0,
        device="0",
    )
    runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))

    assert recorded_kwargs[0]["device"] == "0"


def test_parser_exposes_quality_options() -> None:
    from runtime.yolo_segmentation_runtime import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "--imgsz",
            "1280",
            "--conf",
            "0.25",
            "--iou",
            "0.6",
            "--no-retina-masks",
            "--mask-threshold",
            "0.4",
            "--mask-smoothing",
            "3",
            "--no-keep-largest-component",
            "--no-fill-mask-holes",
            "--no-high-quality-rendering",
        ]
    )

    assert args.imgsz == 1280
    assert args.conf == 0.25
    assert args.iou == 0.6
    assert args.retina_masks is False
    assert args.mask_threshold == 0.4
    assert args.mask_smoothing == 3
    assert args.keep_largest_component is False
    assert args.fill_mask_holes is False
    assert args.high_quality_rendering is False


def test_parser_uses_centralized_absolute_defaults() -> None:
    from runtime.yolo_segmentation_runtime import (
        DEFAULT_CONFIDENCE_THRESHOLD,
        DEFAULT_DEVICE,
        DEFAULT_FILL_MASK_HOLES,
        DEFAULT_HIGH_QUALITY_RENDERING,
        DEFAULT_INSPECTION_MERGE_IOU,
        DEFAULT_INFERENCE_IMGSZ,
        DEFAULT_INPUT_PATH,
        DEFAULT_IOU_THRESHOLD,
        DEFAULT_KEEP_LARGEST_COMPONENT,
        DEFAULT_MODEL_PATH,
        DEFAULT_MASK_SMOOTHING,
        DEFAULT_MASK_THRESHOLD,
        DEFAULT_OUTPUT_PATH,
        DEFAULT_QUIET,
        DEFAULT_RETINA_MASKS,
        build_parser,
    )

    parser = build_parser()
    args = parser.parse_args([])

    assert args.input == str(DEFAULT_INPUT_PATH)
    assert Path(args.input).is_absolute()
    assert args.output == DEFAULT_OUTPUT_PATH
    assert args.model == str(DEFAULT_MODEL_PATH)
    assert Path(args.model).is_absolute()
    assert args.device == DEFAULT_DEVICE
    assert args.imgsz == DEFAULT_INFERENCE_IMGSZ
    assert args.conf == DEFAULT_CONFIDENCE_THRESHOLD
    assert args.iou == DEFAULT_IOU_THRESHOLD
    assert args.retina_masks == DEFAULT_RETINA_MASKS
    assert args.mask_threshold == DEFAULT_MASK_THRESHOLD
    assert args.mask_smoothing == DEFAULT_MASK_SMOOTHING
    assert args.keep_largest_component == DEFAULT_KEEP_LARGEST_COMPONENT
    assert args.fill_mask_holes == DEFAULT_FILL_MASK_HOLES
    assert args.high_quality_rendering == DEFAULT_HIGH_QUALITY_RENDERING
    assert args.inspection_merge_iou == DEFAULT_INSPECTION_MERGE_IOU
    assert args.quiet == DEFAULT_QUIET


def test_parser_help_describes_every_inference_option_in_simplified_chinese() -> None:
    from runtime.yolo_segmentation_runtime import build_parser

    parser = build_parser()
    assert "YOLO11" in parser.description
    assert any("\u4e00" <= character <= "\u9fff" for character in parser.description)
    for action in parser._actions:
        if action.option_strings:
            assert action.help
            assert any("\u4e00" <= character <= "\u9fff" for character in action.help), (
                f"参数 {action.option_strings} 缺少简体中文帮助"
            )


def test_project_relative_inference_paths_are_normalized_absolutely() -> None:
    from runtime.yolo_segmentation_runtime import PROJECT_ROOT, resolve_project_path

    assert resolve_project_path("images/input.jpg") == (
        PROJECT_ROOT / "images/input.jpg"
    ).resolve()
    absolute_path = (PROJECT_ROOT / "model.pt").resolve()
    assert resolve_project_path(absolute_path) == absolute_path
    assert resolve_project_path(None) is None


def test_runtime_script_help_runs_from_workspace_directory() -> None:
    project_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "runtime" / "yolo_segmentation_runtime.py"),
            "--help",
        ],
        cwd=project_root.parent,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "损伤分割推理" in completed.stdout


def test_runtime_core_inference_has_no_temporal_or_boundary_experiment_metadata() -> None:
    from runtime.yolo_segmentation_runtime import YoloSegmentationRuntime

    def fake_loader(_model_path: str):
        def fake_model(_image, **_kwargs):
            return [
                _FakeResult(
                    _FakeMasks([[[1, 0], [0, 0]]]), _FakeBoxes([[1, 2, 3, 4]], [0.9])
                )
            ]

        return fake_model

    runtime = YoloSegmentationRuntime(
        model_path="best.pt", model_loader=fake_loader, mask_smoothing=0
    )
    result = runtime.infer(np.zeros((2, 2, 3), dtype=np.uint8))

    assert result["success"] is True
    assert "temporal_prompt" not in result
    assert "latency_components_ms" not in result
