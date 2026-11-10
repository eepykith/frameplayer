"""Small JSON settings file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from frameplayer.paths import config_dir


@dataclass
class Settings:
    ceiling: int = 4320
    prefer_hw: bool = True
    hwdec_enabled: bool = True
    volume: int = 100
    stats: bool = True
    layout: str = "auto"
    projection: str = "auto"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict) -> Settings:
        settings = cls()
        for key in asdict(settings):
            if key in raw:
                setattr(settings, key, raw[key])
        settings.volume = max(0, min(150, int(settings.volume)))
        settings.ceiling = int(settings.ceiling)
        return settings


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "settings.json")
        self.settings = Settings()
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return
        if isinstance(raw, dict):
            self.settings = Settings.from_json(raw)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.settings.to_json(), indent=2), encoding="utf-8")
