from __future__ import annotations

import json
from pathlib import Path


def _write_coco_annotation(path: Path, *, image_name: str, polygons: list[list[float]]) -> None:
    annotation = {
        "images": [
            {
                "id": 1,
                "file_name": image_name,
                "width": 100,
                "height": 50,
            }
        ],
        "annotations": [
            {
                "id": index + 1,
                "image_id": 1,
                "category_id": 1,
                "segmentation": [polygon],
                "area": 100.0,
                "bbox": [10.0, 10.0, 20.0, 10.0],
                "iscrowd": 0,
            }
            for index, polygon in enumerate(polygons)
        ],
        "categories": [
            {
                "id": 1,
                "name": "damage",
            }
        ],
    }
    path.write_text(json.dumps(annotation), encoding="utf-8")


def test_polygon_annotation_becomes_normalized_yolo_seg_label(tmp_path: Path) -> None:
    from tools.convert_coco_to_yolo_seg import convert_coco_split

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "frame_001.jpg").write_bytes(b"fake-image")

    annotations_path = tmp_path / "instances_train.json"
    _write_coco_annotation(
        annotations_path,
        image_name="frame_001.jpg",
        polygons=[[10.0, 10.0, 30.0, 10.0, 30.0, 20.0, 10.0, 20.0]],
    )

    output_dir = tmp_path / "yolo_damage"
    summary = convert_coco_split(
        images_dir=images_dir,
        annotations_path=annotations_path,
        output_dir=output_dir,
        split="train",
    )

    label_path = output_dir / "labels" / "train" / "frame_001.txt"
    assert label_path.exists()
    line = label_path.read_text(encoding="utf-8").strip()
    assert line == "0 0.100000 0.200000 0.300000 0.200000 0.300000 0.400000 0.100000 0.400000"

    copied_image_path = output_dir / "images" / "train" / "frame_001.jpg"
    assert copied_image_path.exists()
    assert summary["converted_images"] == 1
    assert summary["missing_images"] == []


def test_missing_image_is_reported_in_summary(tmp_path: Path) -> None:
    from tools.convert_coco_to_yolo_seg import convert_coco_split

    images_dir = tmp_path / "images"
    images_dir.mkdir()

    annotations_path = tmp_path / "instances_val.json"
    _write_coco_annotation(
        annotations_path,
        image_name="missing.jpg",
        polygons=[[10.0, 10.0, 30.0, 10.0, 30.0, 20.0, 10.0, 20.0]],
    )

    output_dir = tmp_path / "yolo_damage"
    summary = convert_coco_split(
        images_dir=images_dir,
        annotations_path=annotations_path,
        output_dir=output_dir,
        split="val",
    )

    assert summary["converted_images"] == 0
    assert summary["missing_images"] == ["missing.jpg"]
    assert not (output_dir / "labels" / "val" / "missing.txt").exists()


def test_damage_yaml_is_generated_for_single_class_dataset(tmp_path: Path) -> None:
    from tools.convert_coco_to_yolo_seg import write_dataset_yaml

    output_dir = tmp_path / "yolo_damage"
    output_dir.mkdir()

    yaml_path = write_dataset_yaml(output_dir=output_dir, dataset_name="damage")

    assert yaml_path.exists()
    assert yaml_path.read_text(encoding="utf-8") == (
        "path: datasets/damage\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "names:\n"
        "  0: damage\n"
    )
