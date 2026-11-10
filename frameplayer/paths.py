"""XDG locations for settings, history, and logs."""

from __future__ import annotations

import os
from pathlib import Path


def _root(env: str, default: str) -> Path:
    base = os.environ.get(env) or str(Path.home() / default)
    path = Path(base) / "frameplayer"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    return _root("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _root("XDG_DATA_HOME", ".local/share")


def log_path() -> Path:
    return data_dir() / "mpv.log"
