"""Redirect the frozen renderer to the source-compatible FHL plugin route."""
from __future__ import annotations

import sys
from pathlib import Path

from runtime.fhl_repair_renderer import FhlRepairRenderer


_original_init = FhlRepairRenderer.__init__


def _frozen_init(self, *args, **kwargs):
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    plugin_dir = bundle_root / "fhl_plugin"
    kwargs["script_path"] = plugin_dir / "fhl_runner.mjs"
    kwargs["node_executable"] = str(plugin_dir / "node.exe")
    _original_init(self, *args, **kwargs)
FhlRepairRenderer.__init__ = _frozen_init
