from __future__ import annotations

from pathlib import Path


def test_p2_training_config_is_project_contained() -> None:
    from scripts.train_p2_yolo11s_seg import PROJECT_ROOT, load_train_config

    config = load_train_config()

    assert Path(str(config["data"])).is_file()
    assert Path(str(config["model_yaml"])).is_file()
    assert Path(str(config["pretrained"])).is_file()
    assert Path(str(config["project"])) == PROJECT_ROOT / "runs" / "segment"
    assert config["imgsz"] == 960
    assert config["epochs"] == 100
    assert config["batch"] == 4
    assert config["mask_ratio"] == 2
    assert config["close_mosaic"] == 10
    assert "检测" not in "\n".join(str(value) for value in config.values())
    assert "PycharmProjects" not in "\n".join(str(value) for value in config.values())


def test_p2_training_dry_run_reports_resolved_local_paths(capsys) -> None:
    from scripts.train_p2_yolo11s_seg import main

    main(["--dry-run"])

    output = capsys.readouterr().out
    assert '"model_yaml"' in output
    assert '"pretrained"' in output
    assert "检测" not in output
    assert "PycharmProjects" not in output


def test_p2_architecture_builds_expected_segmentation_strides() -> None:
    from ultralytics import YOLO

    project_root = Path(__file__).resolve().parents[1]
    architecture = project_root / "configs" / "yolo11s-seg-p2.yaml"
    model = YOLO(str(architecture))

    assert model.task == "segment"
    assert len(model.names) == 8
    assert tuple(float(value) for value in model.model.stride.tolist()) == (
        4.0,
        8.0,
        16.0,
        32.0,
    )


def test_production_sources_do_not_reference_external_delivery_directory() -> None:
    project_root = Path(__file__).resolve().parents[1]
    checked_roots = ("configs", "deployment", "packaging", "runtime", "scripts")
    suffixes = {".bat", ".json", ".md", ".ps1", ".py", ".spec", ".yaml", ".yml"}
    forbidden = ("E:\\桌面\\海之子\\检测", "E:/桌面/海之子/检测", "PycharmProjects")
    violations: list[str] = []

    for root_name in checked_roots:
        for path in (project_root / root_name).rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(token in text for token in forbidden):
                violations.append(str(path.relative_to(project_root)))

    assert violations == []


def test_packaging_uses_only_project_formal_p2_checkpoint() -> None:
    project_root = Path(__file__).resolve().parents[1]
    pyinstaller_spec = (project_root / "packaging" / "damage_workflow_desktop.spec").read_text(
        encoding="utf-8"
    )
    reproduction_script = (
        project_root / "packaging" / "create_reproduction_bundle.ps1"
    ).read_text(encoding="utf-8")

    assert 'PROJECT_ROOT / "models" / "best.pt"' in pyinstaller_spec
    assert 'Join-Path $ProjectRoot "models\\best.pt"' in reproduction_script
    assert "damage_yolo11s_from_scratch_960_300" not in reproduction_script
    assert '"models\\last.pt"' not in reproduction_script
    assert '"reference_run"' not in reproduction_script
