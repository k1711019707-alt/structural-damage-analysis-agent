"""Small helpers shared by the executable knowledge-pipeline smoke launchers."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence


PROJECT_ROOT = Path(r"E:\桌面\海之子\YOLO11-seg")
TEST_ROOT = PROJECT_ROOT / "knowledge_pipeline" / "test"
RESULTS_ROOT = TEST_ROOT / "results"
SAMPLE_PDF = PROJECT_ROOT / "knowledge_base" / "source_files" / "GB 55034-2022 建筑与市政施工现场安全卫生与职业健康通用规范.pdf"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_entrypoint(name: str, entrypoint: Callable[[list[str]], int], argv: Sequence[str]) -> int:
    """Run one existing module entrypoint and print a consistent status line."""
    started = time.perf_counter()
    try:
        code = int(entrypoint([str(item) for item in argv]))
        elapsed = time.perf_counter() - started
        print(f"[{name}] status={'success' if code == 0 else 'failed'} elapsed={elapsed:.3f}s exit_code={code}")
        return code
    except Exception as exc:
        elapsed = time.perf_counter() - started
        print(f"[{name}] status=failed elapsed={elapsed:.3f}s error={type(exc).__name__}: {exc}")
        return 1


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_line(path: Path) -> str:
    if not path.exists():
        return f"output={path} exists=False"
    return f"output={path} exists=True bytes={path.stat().st_size}"


def ensure_paths(*paths: Path) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)


def print_header(name: str, input_path: Path, output_path: Path) -> None:
    print(f"[{name}] input={input_path}")
    print(f"[{name}] output={output_path}")
