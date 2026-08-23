"""Loading and light validation of the YAML config files."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Config file missing: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_sources(enabled_only: bool = True) -> list[dict]:
    data = _load("sources.yaml")
    sources = data.get("sources", [])
    if enabled_only:
        sources = [s for s in sources if s.get("enabled", True)]
    return sources


def load_categories() -> dict:
    return _load("categories.yaml")


def load_settings() -> dict:
    return _load("settings.yaml")


def load_watchlists() -> list[dict]:
    data = _load("watchlists.yaml")
    return data.get("watchlists", [])


def find_watchlist(name: str) -> dict | None:
    for wl in load_watchlists():
        if wl.get("name") == name:
            return wl
    return None
