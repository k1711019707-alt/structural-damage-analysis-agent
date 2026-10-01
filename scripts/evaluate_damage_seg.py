from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a trained damage segmentation checkpoint.")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", choices=["val", "test"], default="val")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--conf", type=float, default=0.001)
    parser.add_argument("--name", required=True)
    parser.add_argument("--project", type=Path, default=Path("runs/segment/evaluation"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model = YOLO(str(args.weights))
    metrics = model.val(
        data=str(args.data),
        split=args.split,
        imgsz=args.imgsz,
        batch=args.batch,
        conf=args.conf,
        device="0",
        workers=4,
        plots=True,
        project=str(args.project),
        name=args.name,
        exist_ok=True,
    )
    names = metrics.names
    class_rows = []
    for class_id, class_name in names.items():
        box = metrics.box.class_result(class_id)
        mask = metrics.seg.class_result(class_id)
        class_rows.append(
            {
                "class_id": class_id,
                "class_name": class_name,
                "box_precision": float(box[0]),
                "box_recall": float(box[1]),
                "box_map50": float(box[2]),
                "box_map50_95": float(box[3]),
                "mask_precision": float(mask[0]),
                "mask_recall": float(mask[1]),
                "mask_map50": float(mask[2]),
                "mask_map50_95": float(mask[3]),
            }
        )
    summary = {
        "weights": str(args.weights.resolve()),
        "data": str(args.data.resolve()),
        "split": args.split,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "conf": args.conf,
        "box": {
            "precision": float(metrics.box.mp),
            "recall": float(metrics.box.mr),
            "map50": float(metrics.box.map50),
            "map50_95": float(metrics.box.map),
        },
        "mask": {
            "precision": float(metrics.seg.mp),
            "recall": float(metrics.seg.mr),
            "map50": float(metrics.seg.map50),
            "map50_95": float(metrics.seg.map),
        },
        "targets": {
            "mask_map50_at_least_0_90": bool(metrics.seg.map50 >= 0.90),
            "mask_precision_at_least_0_90": bool(metrics.seg.mp >= 0.90),
        },
        "per_class": class_rows,
    }
    output_dir = args.project / args.name
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output_dir / "per_class_metrics.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(class_rows[0]))
        writer.writeheader()
        writer.writerows(class_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
