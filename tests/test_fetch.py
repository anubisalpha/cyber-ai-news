"""Tests for feed parsing (JSON parsers + RSS, no real network)."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import fetch  # noqa: E402

SETTINGS = {"fetch": {"max_items_per_source": 50, "user_agent": "test"}}


# ---- JSON parsers ----
def test_cisa_kev_parser():
    data = {"vulnerabilities": [
        {"cveID": "CVE-2026-1", "vulnerabilityName": "Bad Bug",
         "shortDescription": "does bad things", "dateAdded": "2026-08-20"},
    ]}
    out = fetch._parse_cisa_kev(data, {"name": "KEV", "weight": 5}, SETTINGS["fetch"])
    assert len(out) == 1
    a = out[0]
    assert a.title.startswith("CVE-2026-1") and a.domain == "cyber"
    assert "nvd.nist.gov" in a.url
    assert a.published.startswith("2026-08-20")


def test_cisa_kev_bad_date_is_none():
    data = {"vulnerabilities": [{"cveID": "CVE-x", "dateAdded": "not-a-date"}]}
    out = fetch._parse_cisa_kev(data, {"name": "KEV"}, SETTINGS["fetch"])
    assert out[0].published is None


def test_nvd_cve_parser_picks_english_desc():
    data = {"vulnerabilities": [
        {"cve": {"id": "CVE-2026-9", "published": "2026-08-19T00:00:00",
                 "descriptions": [
                     {"lang": "es", "value": "spanish"},
                     {"lang": "en", "value": "the real description"},
                 ]}},
    ]}
    out = fetch._parse_nvd_cve(data, {"name": "NVD", "weight": 5}, SETTINGS["fetch"])
    assert out[0].summary == "the real description"
    assert out[0].title == "CVE-2026-9"


def test_clean_strips_html():
    assert fetch._clean("<p>hello <b>world</b></p>") == "hello world"


def test_json_dispatch_requires_known_parser():
    import pytest
    with pytest.raises(ValueError):
        fetch.fetch_json({"name": "X", "parser": "nope", "url": "http://x"}, SETTINGS)


# ---- RSS (monkeypatched feedparser) ----
def test_fetch_rss_maps_entries(monkeypatch):
    fake = types.SimpleNamespace(entries=[
        {"title": "Story A", "link": "https://a/1", "summary": "<p>sum</p>",
         "published_parsed": (2026, 8, 20, 10, 0, 0, 0, 0, 0)},
        {"title": "Story B", "link": "https://a/2", "summary": "plain"},
    ])
    monkeypatch.setattr(fetch.feedparser, "parse", lambda *a, **k: fake)
    src = {"name": "Feed", "url": "https://a/feed", "domain": "ai", "weight": 3}
    out = fetch.fetch_rss(src, SETTINGS)
    assert [a.title for a in out] == ["Story A", "Story B"]
    assert out[0].domain == "ai" and out[0].source == "Feed"
    assert out[0].published.startswith("2026-08-20")
    assert out[1].published is None  # no date fields


def test_fetch_source_unknown_type():
    import pytest
    with pytest.raises(ValueError):
        fetch.fetch_source({"name": "X", "type": "carrier-pigeon"}, SETTINGS)
