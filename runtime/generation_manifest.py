from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def redact_secret_text(value: str) -> str:
    redacted = re.sub(r"\bsk-[A-Za-z0-9._-]{8,}\b", "<redacted>", value)
    return re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1<redacted>",
        redacted,
    )


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


def atomic_write_text(path: str | Path, content: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)
    return target


def merge_profile_manifest(
    path: str | Path,
    *,
    profile_name: str,
    profile_manifest: dict[str, Any],
    outputs: dict[str, str],
    fallback: bool,
    fallback_reason: str = "",
) -> Path:
    target = Path(path)
    payload: dict[str, Any] = {}
    if target.is_file():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    entry = dict(profile_manifest)
    entry["outputs"] = dict(outputs)
    entry["fallback"] = bool(fallback)
    entry["fallback_reason"] = redact_secret_text(str(fallback_reason))[:1000] if fallback else ""
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        payload["profiles"] = profiles
    profiles[profile_name] = entry
    return atomic_write_json(target, payload)
