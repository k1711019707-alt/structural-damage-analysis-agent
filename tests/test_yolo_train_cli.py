from __future__ import annotations


def test_train_cli_loads_values_from_config_file(tmp_path) -> None:
    from scripts.train_yolo11s_seg import PROJECT_ROOT, build_train_config

    config_path = tmp_path / "train.yaml"
    config_path.write_text(
        "\n".join(
            [
                "data: custom/data.yaml",
                "model: custom-model.pt",
                "imgsz: 640",
                "epochs: 20",
                "batch: 2",
                "device: cpu",
                "project: custom/results",
                "name: config_run",
            ]
        ),
        encoding="utf-8",
    )

    config = build_train_config(["--config", str(config_path)])

    assert config == {
        "data": str((PROJECT_ROOT / "custom/data.yaml").resolve()),
        "model": str((PROJECT_ROOT / "custom-model.pt").resolve()),
        "imgsz": 640,
        "epochs": 20,
        "batch": 2,
        "device": "cpu",
        "project": str((PROJECT_ROOT / "custom/results").resolve()),
        "name": "config_run",
        "dry_run": False,
    }


def test_train_cli_command_line_values_override_config_file(tmp_path) -> None:
    from scripts.train_yolo11s_seg import PROJECT_ROOT, build_train_config

    config_path = tmp_path / "train.yaml"
    config_path.write_text(
        "\n".join(
            [
                "data: custom/data.yaml",
                "model: custom-model.pt",
                "imgsz: 640",
                "epochs: 20",
                "batch: 2",
                "device: cpu",
                "project: custom/results",
                "name: config_run",
            ]
        ),
        encoding="utf-8",
    )

    config = build_train_config(["--config", str(config_path), "--batch", "4", "--name", "override_run"])

    assert config["batch"] == 4
    assert config["name"] == "override_run"
    assert config["data"] == str((PROJECT_ROOT / "custom/data.yaml").resolve())


def test_train_cli_preserves_extra_training_keys_from_config(tmp_path) -> None:
    from scripts.train_yolo11s_seg import build_train_config

    config_path = tmp_path / "train.yaml"
    config_path.write_text(
        "\n".join(
            [
                "data: custom/data.yaml",
                "model: custom-model.pt",
                "imgsz: 640",
                "epochs: 20",
                "batch: 2",
                "device: cpu",
                "project: custom/results",
                "name: config_run",
                "workers: 0",
                "patience: 30",
                "cache: ram",
                "plots: false",
                "deterministic: false",
            ]
        ),
        encoding="utf-8",
    )

    config = build_train_config(["--config", str(config_path)])

    assert config["workers"] == 0
    assert config["patience"] == 30
    assert config["cache"] == "ram"
    assert config["plots"] is False
    assert config["deterministic"] is False


def test_train_cli_allows_worker_and_patience_overrides(tmp_path) -> None:
    from scripts.train_yolo11s_seg import build_train_config

    config_path = tmp_path / "train.yaml"
    config_path.write_text(
        "\n".join(
            [
                "data: custom/data.yaml",
                "model: custom-model.pt",
                "imgsz: 640",
                "epochs: 20",
                "batch: 2",
                "device: cpu",
                "project: custom/results",
                "name: config_run",
                "workers: 8",
                "patience: 100",
            ]
        ),
        encoding="utf-8",
    )

    config = build_train_config(
        [
            "--config",
            str(config_path),
            "--workers",
            "0",
            "--patience",
            "30",
        ]
    )

    assert config["workers"] == 0
    assert config["patience"] == 30


def test_train_cli_uses_default_project_config() -> None:
    from pathlib import Path

    from scripts.train_yolo11s_seg import DEFAULT_CONFIG_PATH, build_train_config

    config = build_train_config([])

    assert DEFAULT_CONFIG_PATH == Path(__file__).resolve().parents[1] / "configs" / "yolo11_seg_train.yaml"
    assert DEFAULT_CONFIG_PATH.is_absolute()
    assert Path(config["data"]).is_file()
    assert Path(config["model"]).is_file()
    assert Path(config["project"]).is_absolute()
    assert config["imgsz"] == 960
    assert config["epochs"] == 100
    assert config["batch"] == 4
    assert config["device"] == "0"
    assert Path(config["project"]) == Path(__file__).resolve().parents[1] / "runs" / "segment"
    assert config["name"] == "p2_yolo11s_seg_960_continue"
    assert config["workers"] == 0
    assert config["patience"] == 30
    assert config["cache"] is False
    assert config["plots"] is True
    assert config["deterministic"] is True


def test_train_cli_resolves_required_arguments() -> None:
    from scripts.train_yolo11s_seg import PROJECT_ROOT, build_train_config

    config = build_train_config(
        [
            "--data",
            "../dataset_yolo11_seg/dataset.yaml",
            "--model",
            "models/best.pt",
            "--imgsz",
            "1024",
            "--epochs",
            "150",
            "--batch",
            "8",
            "--device",
            "0",
            "--project",
            "runs/segment",
            "--name",
            "damage_cli_test",
        ]
    )

    assert config["data"] == str((PROJECT_ROOT / "../dataset_yolo11_seg/dataset.yaml").resolve())
    assert config["model"] == str((PROJECT_ROOT / "models/best.pt").resolve())
    assert config["imgsz"] == 1024
    assert config["epochs"] == 150
    assert config["batch"] == 8
    assert config["device"] == "0"
    assert config["project"] == str((PROJECT_ROOT / "runs/segment").resolve())
    assert config["name"] == "damage_cli_test"
    assert config["workers"] == 0
    assert config["patience"] == 30
    assert config["cache"] is False
    assert config["plots"] is True
    assert config["deterministic"] is True
    assert config["dry_run"] is False


def test_train_cli_supports_dry_run(capsys) -> None:
    from scripts.train_yolo11s_seg import main

    main(
        [
            "--data",
            "../dataset_yolo11_seg/dataset.yaml",
            "--model",
            "models/best.pt",
            "--imgsz",
            "896",
            "--epochs",
            "10",
            "--batch",
            "2",
            "--device",
            "0",
            "--project",
            "runs/segment",
            "--name",
            "smoke",
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert '"dry_run": true' in output
    assert '"imgsz": 896' in output
    assert '"name": "smoke"' in output


def test_train_passes_extra_training_keys_to_ultralytics(monkeypatch) -> None:
    import sys
    import types

    from scripts.train_yolo11s_seg import PROJECT_ROOT, train

    captured: dict[str, object] = {}

    class FakeYOLO:
        def __init__(self, model_path: str) -> None:
            captured["model_path"] = model_path

        def train(self, **kwargs):
            captured["kwargs"] = kwargs
            return "trained"

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))

    result = train(
        {
            "data": "../dataset_yolo11_seg/dataset.yaml",
            "model": "models/best.pt",
            "imgsz": 1024,
            "epochs": 150,
            "batch": 8,
            "device": "0",
            "project": "runs/segment",
            "name": "damage_cli_test",
            "workers": 0,
            "patience": 30,
            "dry_run": False,
        }
    )

    assert result == "trained"
    assert captured["model_path"] == str((PROJECT_ROOT / "models/best.pt").resolve())
    assert captured["kwargs"]["data"] == str(
        (PROJECT_ROOT / "../dataset_yolo11_seg/dataset.yaml").resolve()
    )
    assert captured["kwargs"]["project"] == str(
        (PROJECT_ROOT / "runs/segment").resolve()
    )
    assert captured["kwargs"]["workers"] == 0
    assert captured["kwargs"]["patience"] == 30


def test_prepare_runtime_environment_sets_kmp_duplicate_lib_ok_on_windows() -> None:
    from scripts.train_yolo11s_seg import prepare_runtime_environment

    env: dict[str, str] = {}

    prepare_runtime_environment(platform_name="nt", environ=env)

    assert env["KMP_DUPLICATE_LIB_OK"] == "TRUE"


def test_prepare_runtime_environment_preserves_existing_kmp_value() -> None:
    from scripts.train_yolo11s_seg import prepare_runtime_environment

    env = {"KMP_DUPLICATE_LIB_OK": "FALSE"}

    prepare_runtime_environment(platform_name="nt", environ=env)

    assert env["KMP_DUPLICATE_LIB_OK"] == "FALSE"


def test_canonical_training_yaml_comments_every_parameter_in_simplified_chinese() -> None:
    import re
    from pathlib import Path

    config_path = Path(__file__).resolve().parents[1] / "configs" / "yolo11_seg_train.yaml"
    lines = config_path.read_text(encoding="utf-8").splitlines()
    parameter_lines = [
        line for line in lines if line.strip() and not line.lstrip().startswith("#")
    ]

    assert parameter_lines
    for line in parameter_lines:
        assert "#" in line, f"参数缺少行内中文注释：{line}"
        comment = line.split("#", 1)[1]
        assert re.search(r"[\u4e00-\u9fff]", comment), f"注释不是简体中文：{line}"
