from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_INPUT = WORKSPACE_ROOT / "dataset_yolo11_seg" / "images" / "test"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _read_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read integration image: {path}")
    return image


def verify(input_dir: str | Path = DEFAULT_INPUT, *, limit: int = 3) -> dict[str, object]:
    from PySide6.QtWidgets import QApplication

    from runtime.bundled_model import (
        validate_packaged_model_artifacts,
        validate_segmentation_model_contract,
    )
    from runtime.damage_workflow_gui import RecognitionWorker
    from runtime.yolo_segmentation_runtime import DEFAULT_MODEL_PATH, YoloSegmentationRuntime
    import runtime.assistant.context as assistant_context

    source = Path(input_dir).resolve()
    images = sorted(
        (
            path
            for path in source.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
        ),
        key=lambda path: path.name.casefold(),
    )[:limit]
    if len(images) < 2:
        raise ValueError(f"At least two integration images are required under: {source}")

    packaged_paths = validate_packaged_model_artifacts()
    runtime = YoloSegmentationRuntime(
        model_path=DEFAULT_MODEL_PATH,
        device="0",
        imgsz=960,
    )
    validate_segmentation_model_contract(runtime._ensure_model())

    runtime_results = []
    for path in images:
        output = runtime.infer_inspection(_read_image(path))
        runtime_results.append(
            {
                "image": path.name,
                "success": output["success"],
                "findings": len(output["damage_findings"]),
                "classes": [item["class_name"] for item in output["damage_findings"]],
                "latency_ms": round(float(output["latency_ms"]), 3),
            }
        )

    QApplication.instance() or QApplication([])
    assistant_context.record_detection_snapshot = lambda *_args, **_kwargs: None
    captured: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="p2-gui-smoke-") as temp_dir:
        output_dir = Path(temp_dir) / "output"
        worker = RecognitionWorker(
            source,
            output_dir,
            str(DEFAULT_MODEL_PATH),
            "P2 integration smoke",
            images[:2],
        )
        worker.completed.connect(lambda payload: captured.update(payload))
        worker.failed.connect(lambda message: captured.update({"failure": message}))
        worker.run()
        summary = captured.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}
        gui_worker = {
            "status": summary.get("status"),
            "result_count": len(summary.get("results", [])),
            "result_statuses": [item.get("status") for item in summary.get("results", [])],
            "model_path": summary.get("resolved_model_path"),
            "device": summary.get("inference_device"),
            "gpu_acceleration": summary.get("gpu_acceleration"),
            "summary_written": (output_dir / "batch_summary.json").is_file(),
            "failure": captured.get("failure"),
        }

    return {
        "model": str(packaged_paths.pt),
        "sha256": runtime.get_model_provenance()["sha256"],
        "inference_provenance": runtime.get_inference_provenance(),
        "runtime_results": runtime_results,
        "gui_worker": gui_worker,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the project-contained P2 damage model through runtime and GUI dispatch.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(verify(args.input, limit=args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
