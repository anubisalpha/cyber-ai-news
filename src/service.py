"""Orchestration: fetch -> classify -> (LLM fallback) -> dedupe -> store -> query."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .classify import Classifier
from .dedupe import cluster_duplicates
from .fetch import fetch_source
from .llm_classify import LLMClassifier
from .models import Article, utcnow_iso
from .storage import Storage

ROOT = Path(__file__).resolve().parent.parent


class NewsService:
    def __init__(self):
        self.settings = config.load_settings()
        self.categories_cfg = config.load_categories()
        self.classifier = Classifier(self.categories_cfg, self.settings)
        self.llm = LLMClassifier(self.categories_cfg, self.settings)

        storage_cfg = self.settings.get("storage", {})
        db_file = storage_cfg.get("db_file", "data/news.db")
        self.db_path = ROOT / db_file
        first_time = not self.db_path.exists()
        self.store = Storage(self.db_path)

        # One-time migration from the legacy JSON cache, if present.
        if first_time:
            legacy = ROOT / storage_cfg.get("data_file", "data/articles.json")
            imported = self.store.import_json(legacy)
            if imported:
                print(f"Migrated {imported} articles from {legacy.name} into SQLite.")

    # ---- pipeline --------------------------------------------------------
    def refresh(self, verbose: bool = True) -> int:
        import uuid
        run_id = uuid.uuid4().hex
        db_sources = self.store.list_sources(enabled_only=True)
        sources = db_sources if db_sources else config.load_sources(enabled_only=True)
        collected: list[Article] = []
        for src in sources:
            try:
                items = fetch_source(src, self.settings)
                collected.extend(items)
                self.store.record_fetch(src["name"], ok=True, count=len(items),
                                        error=None, run_id=run_id)
                if verbose:
                    print(f"  [ok]   {src['name']}: {len(items)} items")
            except Exception as exc:  # noqa: BLE001 - keep going on a bad feed
                self.store.record_fetch(src["name"], ok=False, count=0,
                                        error=str(exc)[:300], run_id=run_id)
                if verbose:
                    print(f"  [fail] {src['name']}: {exc}")

        # 1. Rule-based classification.
        for art in collected:
            self.classifier.classify(art)

        # 2. Optional LLM fallback for whatever the rules left uncategorized.
        rescued = self.llm.classify_uncategorized(collected, verbose=verbose)
        if verbose and rescued:
            print(f"  LLM fallback re-tagged {rescued} uncategorized items")

        # 3. Age filter + stamp first_seen, then upsert into SQLite.
        collected = self._filter_by_age(collected)
        now = utcnow_iso()
        for a in collected:
            a.first_seen = a.first_seen or now  # storage COALESCE keeps the original
        self.store.upsert_many(collected)

        # 4. Near-duplicate clustering across the whole corpus.
        self._recluster()

        # 5. Retention trim.
        removed = self.store.trim_history(
            self.settings.get("storage", {}).get("history_retention_days", 0)
        )

        # 6. Watchlist alerts (no-op unless alerts.enabled).
        from .alerts import AlertEngine
        AlertEngine(self).check_and_notify(verbose=verbose)

        total = self.store.count()
        if verbose:
            extra = f" (trimmed {removed})" if removed else ""
            print(f"Stored {total} articles in {self.db_path.name}{extra}")
        return total

    def _recluster(self) -> None:
        dcfg = self.settings.get("dedupe", {})
        if not dcfg.get("enabled", True):
            return
        articles = self.store.all()
        changed = cluster_duplicates(
            articles,
            threshold=dcfg.get("title_similarity", 0.7),
            cross_source_only=dcfg.get("cross_source_only", True),
            max_days_apart=dcfg.get("max_days_apart", 3),
        )
        if changed:
            self.store.replace_all(changed)

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
        domain=None, category=None, severity=None, region=None,
        source=None, q=None, limit=None, sort=None, offset=0,
        include_duplicates=True,
    ):
        """Return a list of Articles (back-compat). Use query_page for totals."""
        items, _ = self.query_page(
            domain=domain, category=category, severity=severity, region=region,
            source=source, q=q, limit=limit, sort=sort, offset=offset,
            include_duplicates=include_duplicates,
        )
        return items

    def query_page(
        self,
        domain=None, category=None, severity=None, region=None,
        source=None, q=None, limit=None, sort=None, offset=0,
        include_duplicates=True, source_list: list[str] | None = None,
    ) -> tuple[list[Article], int]:
        sort = sort or self.settings.get("output", {}).get("default_sort", "date")
        limit = limit or self.settings.get("output", {}).get("default_limit", 25)
        items, total = self.store.query(
            domain=domain, category=category, severity=severity, region=region,
            source=source, q=q, sort=sort, limit=limit, offset=offset,
            source_list=source_list,
        )
        if not include_duplicates:
            items = [a for a in items if not a.duplicate_of]
        return items, total

    # ---- aggregates ------------------------------------------------------
    def stats(self) -> dict:
        return self.store.stats()

    # ---- watchlists ------------------------------------------------------
    _WL_FIELDS = ("domain", "category", "severity", "region", "source", "q")

    def watchlists(self) -> list[dict]:
        """All watchlists with a current match count."""
        out = []
        for wl in config.load_watchlists():
            filters = {k: v for k, v in wl.get("filters", {}).items()
                       if k in self._WL_FIELDS}
            _, total = self.store.query(limit=1, **filters)
            out.append({
                "name": wl.get("name"),
                "label": wl.get("label", wl.get("name")),
                "filters": filters,
                "alert": bool(wl.get("alert", False)),
                "count": total,
            })
        return out

    def watchlist_page(self, name, limit=None, offset=0, sort=None,
                       include_duplicates=False):
        wl = config.find_watchlist(name)
        if not wl:
            return None
        filters = {k: v for k, v in wl.get("filters", {}).items()
                   if k in self._WL_FIELDS}
        items, total = self.query_page(
            limit=limit, offset=offset, sort=sort,
            include_duplicates=include_duplicates, **filters,
        )
        return {"name": wl["name"], "label": wl.get("label", wl["name"]),
                "filters": filters, "total": total,
                "articles": [a.to_dict() for a in items]}

    def source_health(self) -> dict:
        rows = self.store.health()
        for r in rows:
            if not r["ok"]:
                r["status"] = "failing"
            elif not r["last_count"]:
                r["status"] = "empty"
            else:
                r["status"] = "ok"
        problems = [r for r in rows if r["status"] != "ok"]
        return {
            "sources": rows,
            "problem_count": len(problems),
            "failing": [r["name"] for r in rows if r["status"] == "failing"],
            "empty": [r["name"] for r in rows if r["status"] == "empty"],
        }

    def overview(self, highlights: int = 8, source_list: list[str] | None = None) -> dict:
        """A one-call summary for the overview dashboard."""
        stats = self.store.stats()
        now = datetime.now(timezone.utc)

        def _recent(sort="date", **filters):
            items, total = self.store.query(sort=sort, limit=highlights * 2,
                                            source_list=source_list, **filters)
            items = [a for a in items if not a.duplicate_of][:highlights]
            return [a.to_dict() for a in items], total

        latest, _ = _recent()
        critical, critical_n = _recent(severity="critical")
        intersection, intersection_n = _recent(category="intersection")

        return {
            "total": stats["total"],
            "by_domain": stats["by_domain"],
            "by_severity": stats["by_severity"],
            "top_categories": list(stats["by_category"].items())[:10],
            "top_sources": list(stats["by_source"].items())[:8],
            "new_last_24h": self.store.new_since((now - timedelta(days=1)).isoformat()),
            "new_last_7d": self.store.new_since((now - timedelta(days=7)).isoformat()),
            "critical_count": critical_n,
            "intersection_count": intersection_n,
            "latest": latest,
            "critical": critical,
            "intersection": intersection,
        }
