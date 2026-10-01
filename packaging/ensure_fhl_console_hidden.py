from __future__ import annotations

import re
import sys
from pathlib import Path


def patch_script(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"(\s*maxBuffer: 10 \* 1024 \* 1024,\r?\n)(?!\s*windowsHide\s*:)(\s*env:)",
        r"\1      windowsHide: true,\n\2",
        text,
        count=1,
    )
    text = re.sub(
        r"(\s*maxBuffer: 1024 \* 1024,\r?\n)(?!\s*windowsHide\s*:)(\s*env:)",
        r"\1      windowsHide: true,\n\2",
        text,
        count=1,
    )
    text = text.replace(
        '], { encoding: "utf8" });',
        '], { encoding: "utf8", windowsHide: true });',
    )
    if len(re.findall(r"windowsHide\s*:\s*true", text)) < 4:
        raise RuntimeError(f"FHL plugin does not expose four hidden PowerShell launches: {path}")
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ensure_fhl_console_hidden.py <generate.mjs>")
    patch_script(Path(sys.argv[1]).resolve())
