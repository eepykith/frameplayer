"""Recent URLs and favorites, stored as JSON in the XDG data directory."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from frameplayer.paths import data_dir


@dataclass
class Entry:
    url: str
    title: str
    favorite: bool = False
    played_at: str = ""
    quality: str = ""
    layout: str = "auto"
    projection: str = "auto"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict) -> Entry:
        return cls(
            url=str(raw.get("url") or ""),
            title=str(raw.get("title") or raw.get("url") or ""),
            favorite=bool(raw.get("favorite")),
            played_at=str(raw.get("played_at") or ""),
            quality=str(raw.get("quality") or ""),
            layout=str(raw.get("layout") or "auto"),
            projection=str(raw.get("projection") or "auto"),
        )


class Library:
    MAX_HISTORY = 40

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "library.json")
        self.entries: list[Entry] = []
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.entries = []
            return
        self.entries = [Entry.from_json(item) for item in raw if isinstance(item, dict) and item.get("url")]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [entry.to_json() for entry in self.entries]
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def remember(self, url: str, title: str, *, quality: str = "", layout: str = "auto", projection: str = "auto") -> Entry:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        existing = next((entry for entry in self.entries if entry.url == url), None)
        if existing:
            existing.title = title or existing.title
            existing.played_at = now
            if quality:
                existing.quality = quality
            existing.layout = layout or existing.layout
            existing.projection = projection or existing.projection
            self.entries.remove(existing)
            self.entries.insert(0, existing)
            entry = existing
        else:
            entry = Entry(
                url=url,
                title=title or url,
                played_at=now,
                quality=quality,
                layout=layout,
                projection=projection,
            )
            self.entries.insert(0, entry)
        favorites = [item for item in self.entries if item.favorite]
        recent = [item for item in self.entries if not item.favorite]
        self.entries = favorites + recent[: self.MAX_HISTORY]
        # Favorites stay, but the played order is recency across the whole list.
        self.entries.sort(key=lambda item: item.played_at, reverse=True)
        self.save()
        return entry

    def set_favorite(self, url: str, favorite: bool, title: str = "") -> Entry:
        existing = next((entry for entry in self.entries if entry.url == url), None)
        if existing is None:
            existing = self.remember(url, title or url)
        existing.favorite = favorite
        if title:
            existing.title = title
        self.save()
        return existing

    def is_favorite(self, url: str) -> bool:
        return any(entry.favorite and entry.url == url for entry in self.entries)

    def favorites(self) -> list[Entry]:
        return [entry for entry in self.entries if entry.favorite]

    def history(self) -> list[Entry]:
        return [entry for entry in self.entries if not entry.favorite]

    def get(self, url: str) -> Entry | None:
        return next((entry for entry in self.entries if entry.url == url), None)
