from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import yaml


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SPLIT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}


@dataclass
class Polygon:
    label: str
    points: np.ndarray


@dataclass
class Sample:
    stem: str
    image_path: Path
    json_path: Path
    width: int
    height: int
    polygons: list[Polygon]
    sha256: str

    @property
    def labels(self) -> set[str]:
        return {polygon.label for polygon in self.polygons}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and prepare LabelMe polygons for YOLO11 segmentation.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--augment", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--audit-only", action="store_true")
    return parser.parse_args()


def read_image(path: Path) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to decode image: {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower() if path.suffix.lower() in {".jpg", ".jpeg", ".png"} else ".jpg"
    ok, encoded = cv2.imencode(suffix, image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise ValueError(f"Unable to encode image: {path}")
    encoded.tofile(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def polygon_area(points: np.ndarray) -> float:
    return abs(float(cv2.contourArea(points.astype(np.float32))))


def audit_dataset(source: Path) -> tuple[list[Sample], dict[str, object]]:
    image_dir = source / "imagedata"
    label_dir = source / "labeldata"
    images = {path.stem: path for path in image_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES}
    json_files = {path.stem: path for path in label_dir.glob("*.json")}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    samples: list[Sample] = []
    class_shapes: Counter[str] = Counter()
    class_images: Counter[str] = Counter()
    shape_types: Counter[str] = Counter()
    image_sizes: Counter[str] = Counter()
    point_counts: Counter[int] = Counter()
    empty_json = 0

    for stem in sorted(set(images) | set(json_files)):
        image_path = images.get(stem)
        json_path = json_files.get(stem)
        if image_path is None:
            errors.append({"stem": stem, "reason": "missing_image"})
            continue
        if json_path is None:
            errors.append({"stem": stem, "reason": "missing_json"})
            continue
        try:
            image = read_image(image_path)
            height, width = image.shape[:2]
            image_sizes[f"{width}x{height}"] += 1
            payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            errors.append({"stem": stem, "reason": f"read_error:{exc}"})
            continue

        json_width, json_height = payload.get("imageWidth"), payload.get("imageHeight")
        if json_width != width or json_height != height:
            warnings.append(
                {
                    "stem": stem,
                    "reason": f"dimension_mismatch:json={json_width}x{json_height},decoded={width}x{height}",
                }
            )

        polygons: list[Polygon] = []
        shapes = payload.get("shapes") or []
        if not shapes:
            empty_json += 1
        for shape_index, shape in enumerate(shapes):
            shape_type = str(shape.get("shape_type") or "polygon")
            shape_types[shape_type] += 1
            if shape_type != "polygon":
                warnings.append({"stem": stem, "reason": f"unsupported_shape:{shape_index}:{shape_type}"})
                continue
            label = str(shape.get("label") or "").strip()
            if not label:
                warnings.append({"stem": stem, "reason": f"empty_label:{shape_index}"})
                continue
            try:
                points = np.asarray(shape.get("points") or [], dtype=np.float32).reshape(-1, 2)
            except Exception:
                warnings.append({"stem": stem, "reason": f"invalid_points:{shape_index}"})
                continue
            point_counts[len(points)] += 1
            points[:, 0] = np.clip(points[:, 0], 0, max(width - 1, 0))
            points[:, 1] = np.clip(points[:, 1], 0, max(height - 1, 0))
            if len(np.unique(points, axis=0)) < 3 or polygon_area(points) < 1.0:
                warnings.append({"stem": stem, "reason": f"degenerate_polygon:{shape_index}"})
                continue
            polygons.append(Polygon(label=label, points=points))
            class_shapes[label] += 1
        for label in {polygon.label for polygon in polygons}:
            class_images[label] += 1
        samples.append(
            Sample(
                stem=stem,
                image_path=image_path,
                json_path=json_path,
                width=width,
                height=height,
                polygons=polygons,
                sha256=sha256_file(image_path),
            )
        )

    duplicate_groups = [sorted(group) for group in _groups_by_hash(samples).values() if len(group) > 1]
    report: dict[str, object] = {
        "source": str(source.resolve()),
        "image_files": len(images),
        "json_files": len(json_files),
        "valid_pairs": len(samples),
        "empty_annotation_images": empty_json,
        "polygon_count": sum(class_shapes.values()),
        "classes": sorted(class_shapes),
        "class_shape_counts": dict(class_shapes.most_common()),
        "class_image_counts": dict(class_images.most_common()),
        "shape_types": dict(shape_types),
        "point_count_distribution": {str(key): value for key, value in sorted(point_counts.items())},
        "image_size_counts": dict(image_sizes.most_common()),
        "exact_duplicate_groups": duplicate_groups,
        "errors": errors,
        "warnings": warnings,
    }
    return samples, report


def _groups_by_hash(samples: Iterable[Sample]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for sample in samples:
        groups[sample.sha256].append(sample.stem)
    return groups


def split_samples(samples: list[Sample], seed: int) -> dict[str, list[Sample]]:
    rng = random.Random(seed)
    by_hash: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_hash[sample.sha256].append(sample)
    groups = list(by_hash.values())
    rng.shuffle(groups)

    total = len(samples)
    target_size = {name: total * ratio for name, ratio in SPLIT_RATIOS.items()}
    all_labels = sorted({label for sample in samples for label in sample.labels})
    total_label = Counter(label for sample in samples for label in sample.labels)
    target_label = {
        split: {label: total_label[label] * SPLIT_RATIOS[split] for label in all_labels} for split in SPLIT_RATIOS
    }
    assigned: dict[str, list[Sample]] = {name: [] for name in SPLIT_RATIOS}
    desired_size = dict(target_size)
    desired_label = {split: dict(counts) for split, counts in target_label.items()}
    remaining = list(groups)

    # Iterative multilabel stratification: repeatedly place groups carrying the
    # currently rarest remaining class into the split with the greatest unmet
    # absolute demand for that class. Exact duplicate images are one group.
    while remaining:
        remaining_label = Counter(label for group in remaining for sample in group for label in sample.labels)
        positive = [label for label, count in remaining_label.items() if count > 0]
        if not positive:
            candidate_indices = list(range(len(remaining)))
        else:
            rare_label = min(positive, key=lambda label: (remaining_label[label], label))
            candidate_indices = [
                index for index, group in enumerate(remaining) if any(rare_label in sample.labels for sample in group)
            ]
        candidate_index = max(
            candidate_indices,
            key=lambda index: (
                len({label for sample in remaining[index] for label in sample.labels}),
                -len(remaining[index]),
                rng.random(),
            ),
        )
        group = remaining.pop(candidate_index)
        group_labels = Counter(label for sample in group for label in sample.labels)

        feasible_splits = [
            split
            for split in SPLIT_RATIOS
            if len(assigned[split]) + len(group) <= math.ceil(target_size[split])
        ]
        candidate_splits = feasible_splits or list(SPLIT_RATIOS)

        def split_score(split: str) -> tuple[float, float, float]:
            if positive:
                label_need = desired_label[split].get(rare_label, 0.0)
            else:
                label_need = 0.0
            aggregate_need = sum(max(desired_label[split].get(label, 0.0), 0.0) for label in group_labels)
            return (label_need, desired_size[split], aggregate_need + rng.random() * 1e-8)

        best_split = max(candidate_splits, key=split_score)
        assigned[best_split].extend(group)
        desired_size[best_split] -= len(group)
        for label, count in group_labels.items():
            desired_label[best_split][label] -= count

    for split in assigned:
        assigned[split].sort(key=lambda sample: sample.stem)
    return assigned


def yolo_lines(polygons: list[Polygon], class_to_id: dict[str, int], width: int, height: int) -> list[str]:
    lines: list[str] = []
    for polygon in polygons:
        points = polygon.points.copy()
        points[:, 0] = np.clip(points[:, 0] / width, 0.0, 1.0)
        points[:, 1] = np.clip(points[:, 1] / height, 0.0, 1.0)
        coords = " ".join(f"{value:.6f}" for value in points.reshape(-1))
        lines.append(f"{class_to_id[polygon.label]} {coords}")
    return lines


def transform_sample(image: np.ndarray, polygons: list[Polygon], variant: int) -> tuple[np.ndarray, list[Polygon]]:
    height, width = image.shape[:2]
    transformed: list[Polygon] = []
    if variant % 2 == 0:
        output = cv2.flip(image, 1)
        for polygon in polygons:
            points = polygon.points.copy()
            points[:, 0] = width - 1 - points[:, 0]
            transformed.append(Polygon(polygon.label, points))
        alpha, beta = 1.08, 6
    else:
        angle = 4.0 if variant % 4 == 1 else -4.0
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
        matrix[:, 2] += np.array([width * 0.015, -height * 0.01])
        output = cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
        for polygon in polygons:
            homogeneous = np.column_stack([polygon.points, np.ones(len(polygon.points), dtype=np.float32)])
            points = homogeneous @ matrix.T
            points[:, 0] = np.clip(points[:, 0], 0, width - 1)
            points[:, 1] = np.clip(points[:, 1], 0, height - 1)
            if len(np.unique(points, axis=0)) >= 3 and polygon_area(points) >= 1.0:
                transformed.append(Polygon(polygon.label, points.astype(np.float32)))
        alpha, beta = 0.92, -4
    output = cv2.convertScaleAbs(output, alpha=alpha, beta=beta)
    return output, transformed


def prepare_dataset(samples: list[Sample], report: dict[str, object], output: Path, seed: int, augment: bool) -> None:
    if output.exists():
        raise FileExistsError(f"Output already exists; refusing to overwrite: {output}")
    output.mkdir(parents=True)
    splits = split_samples(samples, seed)
    classes = list(report["classes"])
    class_to_id = {label: index for index, label in enumerate(classes)}
    train_class_counts = Counter(label for sample in splits["train"] for label in sample.labels)
    nonzero_counts = [count for count in train_class_counts.values() if count]
    rare_threshold = float(np.median(nonzero_counts)) * 0.5 if nonzero_counts else 0.0
    manifest_rows: list[dict[str, object]] = []

    for split, split_samples_list in splits.items():
        image_out = output / "images" / split
        label_out = output / "labels" / split
        image_out.mkdir(parents=True)
        label_out.mkdir(parents=True)
        for sample in split_samples_list:
            target_image = image_out / f"{sample.stem}{sample.image_path.suffix.lower()}"
            shutil.copy2(sample.image_path, target_image)
            (label_out / f"{sample.stem}.txt").write_text(
                "\n".join(yolo_lines(sample.polygons, class_to_id, sample.width, sample.height)), encoding="utf-8"
            )
            manifest_rows.append(
                {
                    "split": split,
                    "output_stem": sample.stem,
                    "source_stem": sample.stem,
                    "augmented": False,
                    "sha256": sample.sha256,
                    "classes": "|".join(sorted(sample.labels)),
                }
            )
            if not augment or split != "train" or not sample.polygons:
                continue
            variants = 2 if any(train_class_counts[label] < rare_threshold for label in sample.labels) else 1
            original = read_image(sample.image_path)
            for variant in range(variants):
                transformed_image, transformed_polygons = transform_sample(original, sample.polygons, variant)
                if not transformed_polygons:
                    continue
                aug_stem = f"{sample.stem}__aug{variant + 1}"
                aug_path = image_out / f"{aug_stem}.jpg"
                write_image(aug_path, transformed_image)
                (label_out / f"{aug_stem}.txt").write_text(
                    "\n".join(yolo_lines(transformed_polygons, class_to_id, sample.width, sample.height)), encoding="utf-8"
                )
                manifest_rows.append(
                    {
                        "split": split,
                        "output_stem": aug_stem,
                        "source_stem": sample.stem,
                        "augmented": True,
                        "sha256": sha256_file(aug_path),
                        "classes": "|".join(sorted({polygon.label for polygon in transformed_polygons})),
                    }
                )

    dataset_yaml = {
        "path": str(output.resolve()),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": {index: label for index, label in enumerate(classes)},
    }
    (output / "dataset.yaml").write_text(yaml.safe_dump(dataset_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with (output / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)

    report["seed"] = seed
    report["split_ratios"] = SPLIT_RATIOS
    report["source_split_counts"] = {split: len(items) for split, items in splits.items()}
    report["output_image_counts"] = dict(Counter(str(row["split"]) for row in manifest_rows))
    report["augmented_image_count"] = sum(bool(row["augmented"]) for row in manifest_rows)
    report["split_class_image_counts"] = {
        split: dict(Counter(label for sample in items for label in sample.labels)) for split, items in splits.items()
    }
    report["class_to_id"] = class_to_id
    report["leakage_check"] = check_leakage(manifest_rows)
    (output / "audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def check_leakage(rows: list[dict[str, object]]) -> dict[str, object]:
    source_splits: dict[str, set[str]] = defaultdict(set)
    hash_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        source_splits[str(row["source_stem"])].add(str(row["split"]))
        hash_splits[str(row["sha256"])].add(str(row["split"]))
    source_leaks = {key: sorted(value) for key, value in source_splits.items() if len(value) > 1}
    hash_leaks = {key: sorted(value) for key, value in hash_splits.items() if len(value) > 1}
    return {"source_identity_leaks": source_leaks, "exact_hash_leaks": hash_leaks, "passed": not source_leaks and not hash_leaks}


def main() -> None:
    args = parse_args()
    samples, report = audit_dataset(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.audit_only:
        report_path = args.output if args.output.suffix else args.output / "audit_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    prepare_dataset(samples, report, args.output, args.seed, args.augment)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
