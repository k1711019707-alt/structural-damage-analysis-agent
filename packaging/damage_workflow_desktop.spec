# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir definition for the portable damage-recognition GUI."""

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


PROJECT_ROOT = Path(SPEC).resolve().parents[1]
MODEL_PATH = PROJECT_ROOT / "models" / "best.pt"
MANIFEST_PATH = PROJECT_ROOT / "deployment" / "models" / "model_manifest.json"
STAGE_VALUE = os.environ.get("YOLO11_PORTABLE_STAGE", "").strip()
if not STAGE_VALUE:
    raise SystemExit("YOLO11_PORTABLE_STAGE is required; run packaging/build_portable.ps1")
STAGE_ROOT = Path(STAGE_VALUE).resolve()
if not (STAGE_ROOT / "portable_stage_manifest.json").is_file():
    raise SystemExit(f"Portable resource staging is incomplete: {STAGE_ROOT}")
FHL_HOOK = PROJECT_ROOT / "packaging" / "frozen_fhl_source_route.py"
CONSTRUCTION_RULES = PROJECT_ROOT / "templates" / "construction_plan_rules_v2.md"


def collect_optional(package: str, collector):
    try:
        return collector(package)
    except (ImportError, ModuleNotFoundError):
        return []


def collect_tree(root: Path, destination: str) -> list[tuple[str, str]]:
    entries = []
    for source in root.rglob("*"):
        if source.is_file():
            relative_parent = source.relative_to(root).parent
            target = Path(destination) / relative_parent
            entries.append((str(source), target.as_posix()))
    return entries


datas = [
    (str(MODEL_PATH), "models"),
    (str(MANIFEST_PATH), "models"),
    (str(CONSTRUCTION_RULES), "templates"),
    (str(STAGE_ROOT / "portable_stage_manifest.json"), "."),
]
datas += collect_tree(STAGE_ROOT / "knowledge_base", "knowledge_base")
datas += collect_tree(STAGE_ROOT / "embedding_models", "embedding_models")
datas += collect_tree(STAGE_ROOT / "fhl_plugin", "fhl_plugin")
binaries = []
hiddenimports = [
    "cv2",
    "numpy",
    "openai",
    "onnxruntime",
    "pypdf",
    "fitz",
    "docx",
    "rapidocr",
    "runtime.damage_workflow_gui",
    "runtime.bundled_model",
    "runtime.damage_repair_plan",
    "runtime.fhl_repair_renderer",
    "runtime.siliconflow_repair_renderer",
    "runtime.generation_citations",
    "runtime.generation_context",
    "runtime.generation_manifest",
    "runtime.knowledge_base",
    "runtime.rag_production",
    "runtime.rag_query",
    "runtime.rag_sync",
    "runtime.responses_construction_plan",
    "runtime.responses_damage_report",
    "runtime.settings_models",
    "runtime.settings_store",
    "runtime.startup_diagnostics",
    "runtime.yolo_segmentation_runtime",
]

for package in (
    "PySide6",
    "shiboken6",
    "ultralytics",
    "torch",
    "torchvision",
    "rapidocr",
    "onnxruntime",
    "fitz",
    "cv2",
    "sentence_transformers",
    "transformers",
    "huggingface_hub",
    "tokenizers",
    "safetensors",
    "docling",
    "docling_core",
):
    datas += collect_optional(package, collect_data_files)
    binaries += collect_optional(package, collect_dynamic_libs)

# PyInstaller's maintained hooks already discover Qt, Torch and ONNX runtime
# extension modules.  Collect only the packages whose Python-side plugins are
# imported dynamically by this application; collecting every Torch submodule
# would add several gigabytes of tests and optional backends.
for package in (
    "ultralytics",
    "rapidocr",
    "runtime.assistant",
    "knowledge_pipeline",
    "sentence_transformers",
    "transformers",
    "docling",
    "docling_core",
):
    hiddenimports += collect_optional(package, collect_submodules)

a = Analysis(
    [str(PROJECT_ROOT / "scripts" / "launch_yolo11s_seg_gui.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    excludes=["tkinter", "pytest", "tests"],
    noarchive=False,
    runtime_hooks=[str(FHL_HOOK)],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YOLO11DamageDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="YOLO11DamageDesktop",
)
