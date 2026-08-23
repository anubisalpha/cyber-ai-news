"""Core data types for the news service."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Article:
    """A single news item, normalised across all source types."""

    title: str
    url: str
    source: str
    domain: str                     # cyber | ai | both
    summary: str = ""
    published: Optional[str] = None   # ISO-8601 string, UTC (from the source)
    first_seen: Optional[str] = None  # ISO-8601 string, UTC (when we first fetched it)
    weight: int = 3

    # Tags assigned by the classifier.
    categories: list[str] = field(default_factory=list)
    severity: Optional[str] = None
    regions: list[str] = field(default_factory=list)
    content_types: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        """Stable de-dupe key based on the URL."""
        return hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:16]

    def searchable_text(self, fields: list[str]) -> str:
        parts = []
        for f in fields:
            val = getattr(self, f, "") or ""
            parts.append(str(val))
        return " ".join(parts).lower()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        d = dict(d)
        d.pop("id", None)  # derived, not stored
        return cls(**d)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
