"""Orchestration: fetch -> classify -> dedupe -> store -> query."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .classify import Classifier
from .fetch import fetch_source
from .models import Article, utcnow_iso

ROOT = Path(__file__).resolve().parent.parent


class NewsService:
    def __init__(self):
        self.settings = config.load_settings()
        self.categories_cfg = config.load_categories()
        self.classifier = Classifier(self.categories_cfg, self.settings)
        data_file = self.settings.get("storage", {}).get("data_file", "data/articles.json")
        self.data_path = ROOT / data_file

    # ---- pipeline --------------------------------------------------------
    def refresh(self, verbose: bool = True) -> list[Article]:
        sources = config.load_sources(enabled_only=True)
        collected: list[Article] = []
        for src in sources:
            try:
                items = fetch_source(src, self.settings)
                collected.extend(items)
                if verbose:
                    print(f"  [ok]   {src['name']}: {len(items)} items")
            except Exception as exc:  # noqa: BLE001 - keep going on a bad feed
                if verbose:
                    print(f"  [fail] {src['name']}: {exc}")

        for art in collected:
            self.classifier.classify(art)

        collected = self._filter_by_age(collected)
        merged = self._merge_with_stored(collected)
        merged = self._trim_history(merged)
        self._save(merged)
        if verbose:
            print(f"Stored {len(merged)} articles -> {self.data_path}")
        return merged

    # ---- storage ---------------------------------------------------------
    def _merge_with_stored(self, new: list[Article]) -> list[Article]:
        dedupe = self.settings.get("storage", {}).get("dedupe", True)
        now = utcnow_iso()
        existing = self.load_stored()
        seen_first = {a.id: a.first_seen for a in existing}
        by_id = {a.id: a for a in existing}
        for a in new:
            # Preserve original first_seen if we've seen this URL before,
            # otherwise stamp it now (this is a brand-new fetch).
            a.first_seen = seen_first.get(a.id) or a.first_seen or now
            by_id[a.id] = a  # newest content wins, first_seen preserved above
        merged = list(by_id.values()) if dedupe else existing + new
        merged.sort(key=lambda a: a.published or "", reverse=True)
        return merged

    def _trim_history(self, articles: list[Article]) -> list[Article]:
        """Drop articles first seen longer ago than history_retention_days.

        0 (default) means unlimited history — keep everything.
        """
        days = self.settings.get("storage", {}).get("history_retention_days", 0)
        if not days:
            return articles
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        kept = []
        for a in articles:
            ref = a.first_seen or a.published
            if not ref:
                kept.append(a)
                continue
            try:
                dt = datetime.fromisoformat(ref)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    kept.append(a)
            except ValueError:
                kept.append(a)
        return kept

    def load_stored(self) -> list[Article]:
        if not self.data_path.exists():
            return []
        raw = json.loads(self.data_path.read_text(encoding="utf-8"))
        return [Article.from_dict(d) for d in raw]

    def _save(self, articles: list[Article]) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.write_text(
            json.dumps([a.to_dict() for a in articles], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _filter_by_age(self, articles: list[Article]) -> list[Article]:
        max_age = self.settings.get("fetch", {}).get("max_age_days", 0)
        if not max_age:
            return articles
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)
        kept = []
        for a in articles:
            if not a.published:
                kept.append(a)
                continue
            try:
                dt = datetime.fromisoformat(a.published)
                if dt.tzinfo is None:  # some feeds (e.g. NVD) omit tz -> assume UTC
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    kept.append(a)
            except ValueError:
                kept.append(a)
        return kept

    # ---- query -----------------------------------------------------------
    def query(
        self,
        domain: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        region: str | None = None,
        source: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        sort: str | None = None,
    ) -> list[Article]:
        items = self.load_stored()

        if domain:
            items = [a for a in items if a.domain in (domain, "both")]
        if category:
            items = [a for a in items if any(category in c for c in a.categories)]
        if severity:
            items = [a for a in items if a.severity == severity]
        if region:
            items = [a for a in items if region in a.regions]
        if source:
            items = [a for a in items if source.lower() in a.source.lower()]
        if q:
            needle = q.lower()
            items = [
                a for a in items
                if needle in a.title.lower() or needle in (a.summary or "").lower()
            ]

        sort = sort or self.settings.get("output", {}).get("default_sort", "date")
        if sort == "weight":
            items.sort(key=lambda a: (a.weight, a.published or ""), reverse=True)
        else:
            items.sort(key=lambda a: a.published or "", reverse=True)

        limit = limit or self.settings.get("output", {}).get("default_limit", 25)
        return items[:limit]

    # ---- aggregates ------------------------------------------------------
    def stats(self) -> dict:
        items = self.load_stored()
        by_domain: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for a in items:
            by_domain[a.domain] = by_domain.get(a.domain, 0) + 1
            if a.severity:
                by_severity[a.severity] = by_severity.get(a.severity, 0) + 1
            for c in a.categories:
                by_category[c] = by_category.get(c, 0) + 1
            by_source[a.source] = by_source.get(a.source, 0) + 1
        return {
            "total": len(items),
            "by_domain": by_domain,
            "by_severity": by_severity,
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        }
