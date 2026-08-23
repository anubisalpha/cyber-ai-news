"""Tests for Phase 2 features: source health, digest build, RSS, overview."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from src.models import Article  # noqa: E402
from src.storage import Storage  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "t.db")
    yield s
    s.close()


def art(title, **kw):
    return Article(title=title, url=f"u/{title}", source=kw.pop("source", "s"),
                   domain=kw.pop("domain", "cyber"), **kw)


# ---- source health ----
def test_record_and_read_health(store):
    store.record_fetch("Feed A", ok=True, count=10, error=None)
    store.record_fetch("Feed B", ok=False, count=0, error="boom")
    rows = {r["name"]: r for r in store.health()}
    assert rows["Feed A"]["ok"] == 1 and rows["Feed A"]["last_count"] == 10
    assert rows["Feed B"]["ok"] == 0 and rows["Feed B"]["fail_streak"] == 1


def test_fail_streak_increments_then_resets(store):
    store.record_fetch("F", ok=False, count=0, error="x")
    store.record_fetch("F", ok=False, count=0, error="x")
    assert store.health()[0]["fail_streak"] == 2
    store.record_fetch("F", ok=True, count=5, error=None)
    row = store.health()[0]
    assert row["fail_streak"] == 0 and row["last_success"] is not None


def test_new_since(store):
    store.upsert_many([
        art("recent", first_seen="2026-08-23T00:00:00+00:00"),
        art("old", first_seen="2020-01-01T00:00:00+00:00"),
    ])
    assert store.new_since("2026-08-01T00:00:00+00:00") == 1


# ---- overview + digest via a fake service ----
class _FakeService:
    def __init__(self, store):
        self.store = store
        self.settings = {"digest": {"window_hours": 0, "max_per_section": 5}}

    # minimal overview used by DigestBuilder.build()
    def overview(self, highlights=1):
        from src.service import NewsService
        return NewsService.overview(self, highlights)  # reuse real logic


def test_overview_shape(store):
    store.upsert_many([
        art("crit", severity="critical", published="2026-08-03T00:00:00+00:00"),
        art("plain", domain="ai", published="2026-08-02T00:00:00+00:00"),
    ])
    from src.service import NewsService
    ov = NewsService.overview(_FakeService(store))
    assert ov["total"] == 2
    assert ov["critical_count"] == 1
    assert "latest" in ov and "intersection_count" in ov


def test_digest_build_no_send(store):
    store.upsert_many([
        art("crit vuln", severity="critical", published="2026-08-03T00:00:00+00:00",
            summary="bad"),
    ])
    from src.digest import DigestBuilder
    b = DigestBuilder(_FakeService(store))
    subject, html, total = b.build()
    assert "Digest" in subject
    assert total >= 1
    assert "crit vuln" in html and "<h1" in html
