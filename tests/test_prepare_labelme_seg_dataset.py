from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_labelme_seg_dataset.py"
SPEC = importlib.util.spec_from_file_location("prepare_dataset", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_yolo_lines_are_normalized() -> None:
    polygon = MODULE.Polygon("damage", np.array([[0, 0], [100, 0], [100, 50]], dtype=np.float32))
    line = MODULE.yolo_lines([polygon], {"damage": 0}, 100, 50)[0]
    values = [float(value) for value in line.split()[1:]]
    assert all(0.0 <= value <= 1.0 for value in values)


def test_transform_sample_preserves_valid_polygon() -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    polygon = MODULE.Polygon("damage", np.array([[10, 10], [50, 10], [50, 50], [10, 50]], dtype=np.float32))
    output, polygons = MODULE.transform_sample(image, [polygon], 0)
    assert output.shape == image.shape
    assert len(polygons) == 1
    assert MODULE.polygon_area(polygons[0].points) > 0


def test_leakage_check_detects_cross_split_source() -> None:
    rows = [
        {"source_stem": "a", "split": "train", "sha256": "1"},
        {"source_stem": "a", "split": "val", "sha256": "2"},
    ]
    result = MODULE.check_leakage(rows)
    assert result["passed"] is False
    assert result["source_identity_leaks"] == {"a": ["train", "val"]}


def test_split_samples_balances_single_label_classes() -> None:
    samples = []
    for class_index, label in enumerate(["a", "b"]):
        for index in range(20):
            samples.append(
                MODULE.Sample(
                    stem=f"{label}{index}",
                    image_path=Path(f"{label}{index}.jpg"),
                    json_path=Path(f"{label}{index}.json"),
                    width=10,
                    height=10,
                    polygons=[MODULE.Polygon(label, np.array([[0, 0], [9, 0], [9, 9]], dtype=np.float32))],
                    sha256=f"{class_index}-{index}",
                )
            )
    splits = MODULE.split_samples(samples, seed=42)
    counts = {
        split: {label: sum(label in sample.labels for sample in values) for label in ["a", "b"]}
        for split, values in splits.items()
    }
    assert counts["train"] == {"a": 14, "b": 14}
    assert counts["val"] == {"a": 3, "b": 3}
    assert counts["test"] == {"a": 3, "b": 3}
    assert {split: len(values) for split, values in splits.items()} == {"train": 28, "val": 6, "test": 6}
