"""Versioned local settings persistence and legacy API migration."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from runtime.settings_models import AppSettings


class SettingsStore:
    def __init__(self, path: str | Path, legacy_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.legacy_path = Path(legacy_path) if legacy_path else self.path.with_name("gui_api_config.json")
        self.migration_warning = ""

    def load(self) -> AppSettings:
        if self.path.is_file():
            try:
                settings = AppSettings.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
                if not settings.validate():
                    return settings
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self.migration_warning = f"读取设置失败，将使用默认设置：{type(exc).__name__}"
        if self.legacy_path.is_file():
            try:
                settings = AppSettings.from_legacy(json.loads(self.legacy_path.read_text(encoding="utf-8")))
                self.save(settings)
                backup = self.legacy_path.with_suffix(self.legacy_path.suffix + ".legacy-backup")
                if not backup.exists():
                    shutil.copy2(self.legacy_path, backup)
                self.migration_warning = "已将旧 API 设定迁移到统一设置；旧文件已保留。"
                return settings
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self.migration_warning = f"旧 API 设定迁移失败：{type(exc).__name__}"
        return AppSettings()

    def save(self, settings: AppSettings) -> None:
        errors = settings.validate()
        if errors:
            raise ValueError("；".join(errors))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(settings.to_json(), encoding="utf-8")
        os.replace(temporary, self.path)

    def export_payload(self, settings: AppSettings) -> dict[str, Any]:
        return json.loads(settings.to_json(redact_secrets=True))

    def save_export(self, settings: AppSettings, target: str | Path) -> Path:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.export_payload(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

