"""Tests for near-duplicate clustering."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dedupe import cluster_duplicates  # noqa: E402
from src.models import Article  # noqa: E402


def art(title, source, published="2026-08-20T00:00:00+00:00"):
    return Article(title=title, url=f"u/{title}/{source}", source=source,
                   domain="cyber", published=published)


def test_cross_source_duplicate_merged():
    arts = [
        art("LockBit ransomware gang hits major hospital chain", "BleepingComputer"),
        art("LockBit ransomware gang hits major hospital chain today", "The Hacker News"),
    ]
    cluster_duplicates(arts)
    canon = [a for a in arts if not a.duplicate_of]
    dupes = [a for a in arts if a.duplicate_of]
    assert len(canon) == 1 and len(dupes) == 1
    assert dupes[0].cluster_id == canon[0].id


def test_same_source_templates_not_merged():
    # A single outlet emitting near-identical titles is NOT a duplicate.
    arts = [
        art("CISA Adds Two Known Exploited Vulnerabilities to Catalog", "CISA"),
        art("CISA Adds Four Known Exploited Vulnerabilities to Catalog", "CISA"),
    ]
    cluster_duplicates(arts)
    assert all(a.duplicate_of is None for a in arts)


def test_time_window_blocks_far_apart():
    arts = [
        art("Big breach at MegaCorp exposes millions", "A", "2026-08-01T00:00:00+00:00"),
        art("Big breach at MegaCorp exposes millions", "B", "2026-08-20T00:00:00+00:00"),
    ]
    cluster_duplicates(arts, max_days_apart=3)
    assert all(a.duplicate_of is None for a in arts)


def test_unrelated_not_merged():
    arts = [
        art("New AI chip beats benchmark", "A"),
        art("Ransomware hits city government", "B"),
    ]
    cluster_duplicates(arts)
    assert all(a.duplicate_of is None for a in arts)


def test_no_change_returns_empty():
    arts = [art("Solo story with no pair", "A")]
    # First pass assigns cluster; second pass should report no change.
    cluster_duplicates(arts)
    assert cluster_duplicates(arts) == []
