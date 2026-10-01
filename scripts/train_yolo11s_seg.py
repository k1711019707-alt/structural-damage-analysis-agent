from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "yolo11_seg_train.yaml"
REQUIRED_CONFIG_KEYS = ("data", "model", "imgsz", "epochs", "batch", "device", "project", "name")
OPTIONAL_OVERRIDE_KEYS = ("workers", "patience")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train a YOLO11s-seg model on the local damage dataset.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to the training YAML config file.")
    parser.add_argument("--data")
    parser.add_argument("--model")
    parser.add_argument("--imgsz", type=int)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--batch", type=int)
    parser.add_argument("--device")
    parser.add_argument("--project")
    parser.add_argument("--name")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--patience", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _resolve_project_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _normalize_training_paths(config: dict[str, object]) -> dict[str, object]:
    resolved = dict(config)
    for key in ("data", "model", "project"):
        value = resolved.get(key)
        if value not in (None, ""):
            resolved[key] = str(_resolve_project_path(str(value)))
    return resolved


def _load_config_file(config_path: str | Path) -> dict[str, object]:
    config_path = _resolve_project_path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Training config file does not exist: {config_path}")

    import yaml

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Training config must be a mapping: {config_path}")
    return dict(data)


def _coerce_train_config(config: dict[str, object]) -> dict[str, object]:
    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in config or config[key] in (None, "")]
    if missing:
        raise ValueError(f"Training config is missing required keys: {', '.join(missing)}")
    resolved = _normalize_training_paths(config)
    resolved["imgsz"] = int(config["imgsz"])
    resolved["epochs"] = int(config["epochs"])
    resolved["batch"] = int(config["batch"])
    resolved["device"] = str(config["device"])
    resolved["name"] = str(config["name"])
    if "workers" in resolved and resolved["workers"] is not None:
        resolved["workers"] = int(resolved["workers"])
    if "patience" in resolved and resolved["patience"] is not None:
        resolved["patience"] = int(resolved["patience"])
    resolved["dry_run"] = bool(config.get("dry_run", False))
    return resolved


def build_train_config(argv: list[str] | None = None) -> dict[str, object]:
    args = build_parser().parse_args(argv)
    config = _load_config_file(args.config)
    for key in REQUIRED_CONFIG_KEYS:
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    for key in OPTIONAL_OVERRIDE_KEYS:
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    config["dry_run"] = args.dry_run
    return _coerce_train_config(config)


def prepare_runtime_environment(
    platform_name: str | None = None,
    environ: dict[str, str] | None = None,
) -> None:
    platform_name = os.name if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    if platform_name == "nt":
        environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def train(config: dict[str, object]) -> object:
    prepare_runtime_environment()
    from ultralytics import YOLO

    config = _normalize_training_paths(config)
    model = YOLO(str(config["model"]))
    excluded_keys = {"model", "dry_run"}
    train_kwargs = {key: value for key, value in config.items() if key not in excluded_keys}
    return model.train(**train_kwargs)


def main(argv: list[str] | None = None) -> object | None:
    config = build_train_config(argv)
    if bool(config["dry_run"]):
        print(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True))
        return None
    return train(config)


if __name__ == "__main__":
    main()
