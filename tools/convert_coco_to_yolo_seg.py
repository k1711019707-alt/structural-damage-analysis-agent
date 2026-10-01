from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def _iter_valid_polygons(segmentation) -> tuple[list[list[float]], int]:
    valid_polygons: list[list[float]] = []
    skipped_invalid = 0
    if not isinstance(segmentation, list):
        return valid_polygons, 1

    for polygon in segmentation:
        if not isinstance(polygon, list) or len(polygon) < 6 or len(polygon) % 2 != 0:
            skipped_invalid += 1
            continue
        valid_polygons.append([float(value) for value in polygon])
    return valid_polygons, skipped_invalid


def _normalize_polygon(polygon: list[float], *, width: float, height: float) -> list[float]:
    normalized: list[float] = []
    for index, value in enumerate(polygon):
        divisor = width if index % 2 == 0 else height
        normalized.append(float(value) / float(divisor))
    return normalized


def write_dataset_yaml(output_dir: str | Path, dataset_name: str = "damage") -> Path:
    output_dir = Path(output_dir)
    yaml_path = output_dir / "dataset.yaml"
    yaml_path.write_text(
        "path: datasets/damage\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "names:\n"
        f"  0: {dataset_name}\n",
        encoding="utf-8",
    )
    return yaml_path


def convert_coco_split(
    *,
    images_dir: str | Path,
    annotations_path: str | Path,
    output_dir: str | Path,
    split: str,
) -> dict[str, object]:
    images_dir = Path(images_dir)
    annotations_path = Path(annotations_path)
    output_dir = Path(output_dir)

    with annotations_path.open("r", encoding="utf-8") as file:
        coco = json.load(file)

    image_records = {int(record["id"]): record for record in coco.get("images", [])}
    annotations_by_image_id: dict[int, list[dict[str, object]]] = {}
    for annotation in coco.get("annotations", []):
        image_id = int(annotation["image_id"])
        annotations_by_image_id.setdefault(image_id, []).append(annotation)

    split_images_dir = output_dir / "images" / split
    split_labels_dir = output_dir / "labels" / split
    split_images_dir.mkdir(parents=True, exist_ok=True)
    split_labels_dir.mkdir(parents=True, exist_ok=True)

    converted_images = 0
    missing_images: list[str] = []
    skipped_invalid_polygons = 0
    empty_label_images = 0

    for image_id, image_record in image_records.items():
        file_name = str(image_record["file_name"])
        source_image_path = images_dir / file_name
        if not source_image_path.exists():
            missing_images.append(file_name)
            continue

        shutil.copy2(source_image_path, split_images_dir / file_name)
        width = float(image_record["width"])
        height = float(image_record["height"])

        label_lines: list[str] = []
        for annotation in annotations_by_image_id.get(image_id, []):
            polygons, skipped_count = _iter_valid_polygons(annotation.get("segmentation"))
            skipped_invalid_polygons += skipped_count
            for polygon in polygons:
                normalized_polygon = _normalize_polygon(polygon, width=width, height=height)
                coords = " ".join(f"{value:.6f}" for value in normalized_polygon)
                label_lines.append(f"0 {coords}")

        label_path = split_labels_dir / f"{Path(file_name).stem}.txt"
        label_path.write_text("\n".join(label_lines), encoding="utf-8")
        if not label_lines:
            empty_label_images += 1
        converted_images += 1

    write_dataset_yaml(output_dir=output_dir, dataset_name="damage")
    return {
        "split": split,
        "converted_images": converted_images,
        "missing_images": sorted(missing_images),
        "skipped_invalid_polygons": skipped_invalid_polygons,
        "empty_label_images": empty_label_images,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert COCO segmentation annotations to YOLO segmentation labels.")
    parser.add_argument("--images", required=True, help="Directory containing source images for the split.")
    parser.add_argument("--annotations", required=True, help="Path to the COCO annotation JSON file.")
    parser.add_argument("--output", required=True, help="Output YOLO dataset directory.")
    parser.add_argument("--split", required=True, choices=("train", "val", "test"), help="Dataset split name.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = convert_coco_split(
        images_dir=args.images,
        annotations_path=args.annotations,
        output_dir=args.output,
        split=args.split,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
