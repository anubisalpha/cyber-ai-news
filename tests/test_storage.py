"""Tests for the SQLite storage backend."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from src.models import Article  # noqa: E402
from src.storage import Storage  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def art(title, source="s", domain="cyber", **kw):
    return Article(title=title, url=f"u/{title}/{source}", source=source,
                   domain=domain, **kw)


def test_upsert_and_count(store):
    store.upsert_many([art("a"), art("b", domain="ai")])
    assert store.count() == 2


def test_upsert_preserves_first_seen(store):
    a = art("a", first_seen="2026-01-01T00:00:00+00:00")
    store.upsert_many([a])
    # Re-upsert same URL with a different first_seen -> original kept.
    a2 = art("a", first_seen="2026-06-01T00:00:00+00:00")
    store.upsert_many([a2])
    got = store.all()[0]
    assert got.first_seen == "2026-01-01T00:00:00+00:00"
    assert store.count() == 1  # de-duped by id/url


def test_query_filters_and_pagination(store):
    store.upsert_many([
        art("crit vuln", domain="cyber", severity="critical",
            published="2026-08-03T00:00:00+00:00"),
        art("ai model", domain="ai", published="2026-08-02T00:00:00+00:00"),
        art("minor bug", domain="cyber", severity="low",
            published="2026-08-01T00:00:00+00:00"),
    ])
    items, total = store.query(domain="cyber", limit=50, offset=0)
    assert total == 2 and len(items) == 2

    items, total = store.query(severity="critical", limit=50)
    assert total == 1 and items[0].title == "crit vuln"

    # pagination
    page1, total = store.query(limit=2, offset=0)
    page2, _ = store.query(limit=2, offset=2)
    assert total == 3 and len(page1) == 2 and len(page2) == 1


def test_category_and_region_like_filter(store):
    a = art("x")
    a.categories = ["cyber.malware"]
    a.regions = ["uk"]
    store.upsert_many([a])
    assert store.query(category="malware")[1] == 1
    assert store.query(region="uk")[1] == 1
    assert store.query(category="ai_security")[1] == 0


def test_trim_history(store):
    old = art("old", first_seen="2020-01-01T00:00:00+00:00")
    new = art("new", first_seen="2026-08-20T00:00:00+00:00")
    store.upsert_many([old, new])
    removed = store.trim_history(days=30)
    assert removed == 1 and store.count() == 1


def test_stats(store):
    store.upsert_many([
        art("a", domain="cyber", severity="critical"),
        art("b", domain="ai"),
    ])
    s = store.stats()
    assert s["total"] == 2
    assert s["by_domain"]["cyber"] == 1
    assert s["by_severity"]["critical"] == 1
