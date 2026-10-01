from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from runtime.app_paths import application_resource_root, user_data_root, user_knowledge_base_root
from runtime.bundled_model import resolve_bundled_model_paths, resolve_production_device


APP_VERSION = "2.1.0"


def application_version() -> str:
    manifest = application_resource_root() / "portable_stage_manifest.json"
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            packaged = str(payload.get("product_version") or "").strip()
            if packaged:
                return packaged
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return APP_VERSION


def collect_startup_diagnostics() -> dict[str, object]:
    model = resolve_bundled_model_paths().pt
    try:
        device = resolve_production_device()
        device_payload = {"value": device.value, "label": device.label, "cuda": device.is_cuda}
    except Exception as exc:  # pragma: no cover - defensive startup path
        device_payload = {"value": "cpu", "label": f"CPU ({type(exc).__name__})", "cuda": False}
    try:
        import rapidocr

        ocr_resource = str(Path(rapidocr.__file__).resolve().parent)
    except Exception:
        ocr_resource = None
    return {
        "app": "YOLO11DamageDesktop",
        "version": application_version(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "resource_root": str(application_resource_root()),
        "user_data_root": str(user_data_root()),
        "knowledge_base_root": str(user_knowledge_base_root()),
        "model_path": str(model),
        "model_present": model.is_file(),
        "device": device_payload,
        "ocr_resource": ocr_resource,
    }


def write_startup_diagnostics() -> Path:
    target = user_data_root() / "logs" / "startup_diagnostics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(collect_startup_diagnostics(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
