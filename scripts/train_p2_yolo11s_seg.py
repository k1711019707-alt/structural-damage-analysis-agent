from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "p2_yolo11s_seg_train.yaml"
PATH_KEYS = ("data", "model_yaml", "pretrained", "project")


def _project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_train_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, object]:
    import yaml

    resolved_config = _project_path(config_path)
    payload = yaml.safe_load(resolved_config.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"P2 training config must be a mapping: {resolved_config}")
    config = dict(payload)
    missing = [key for key in ("data", "model_yaml", "pretrained", "project") if not config.get(key)]
    if missing:
        raise ValueError(f"P2 training config is missing: {', '.join(missing)}")
    for key in PATH_KEYS:
        config[key] = str(_project_path(str(config[key])))
    return config


def train(config: dict[str, object]) -> object:
    if os.name == "nt":
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    from ultralytics import YOLO

    model = YOLO(str(config["model_yaml"]))
    model.load(str(config["pretrained"]))
    excluded = {"model_yaml", "pretrained"}
    return model.train(**{key: value for key, value in config.items() if key not in excluded})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train or continue the project-contained P2-YOLO11s segmentation model.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> object | None:
    args = build_parser().parse_args(argv)
    config = load_train_config(args.config)
    if args.dry_run:
        print(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True))
        return None
    return train(config)


if __name__ == "__main__":
    main()
