"""Rule-based keyword classification.

Deterministic, dependency-free tagging. An article can match multiple
categories. Severity is single-valued (highest match wins). Regions and
content types are multi-valued. All matching is case-insensitive and
loosely word-boundary aware so "cve" doesn't match "cover".
"""
from __future__ import annotations

import re
from functools import lru_cache

from .models import Article


def _compile(keyword: str) -> re.Pattern:
    """Word-boundary-ish match. Keeps hyphens/spaces in multi-word phrases."""
    kw = keyword.strip().lower()
    escaped = re.escape(kw)
    # For phrases with spaces, allow flexible whitespace between words.
    escaped = escaped.replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


@lru_cache(maxsize=4096)
def _pattern_for(keyword: str) -> re.Pattern:
    return _compile(keyword)


def _matches(text: str, keywords: list[str], min_len: int) -> bool:
    for kw in keywords:
        if len(kw.strip()) < min_len:
            continue
        if _pattern_for(kw).search(text):
            return True
    return False


class Classifier:
    def __init__(self, categories_cfg: dict, settings: dict):
        self.categories = categories_cfg.get("categories", {})
        self.cross = categories_cfg.get("cross_cutting", {})
        cls_cfg = settings.get("classify", {})
        self.search_fields = cls_cfg.get("search_fields", ["title", "summary"])
        self.fallback = cls_cfg.get("fallback_category", "uncategorized")
        self.min_len = cls_cfg.get("min_keyword_length", 3)

    def classify(self, article: Article) -> Article:
        text = article.searchable_text(self.search_fields)

        # --- categories (multi-valued) ---
        tags: list[str] = []
        for domain, cats in self.categories.items():
            for key, spec in cats.items():
                kws = spec.get("keywords", [])
                if _matches(text, kws, self.min_len):
                    tags.append(f"{domain}.{key}")
        article.categories = tags or [self.fallback]

        # --- severity (single, highest-priority match) ---
        severity_cfg = self.cross.get("severity", {})
        for level in ("critical", "high", "medium", "low"):
            spec = severity_cfg.get(level)
            if spec and _matches(text, spec.get("keywords", []), self.min_len):
                article.severity = level
                break

        # --- regions (multi) ---
        article.regions = [
            name for name, spec in self.cross.get("region", {}).items()
            if _matches(text, spec.get("keywords", []), self.min_len)
        ]

        # --- content types (multi) ---
        article.content_types = [
            name for name, spec in self.cross.get("content_type", {}).items()
            if _matches(text, spec.get("keywords", []), self.min_len)
        ]

        return article
