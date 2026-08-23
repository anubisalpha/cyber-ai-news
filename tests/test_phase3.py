"""Tests for Phase 3: watchlists + alerts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from src import config  # noqa: E402
from src.alerts import AlertEngine  # noqa: E402
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


# ---- config ----
def test_watchlists_config_loads():
    wls = config.load_watchlists()
    assert any(w["name"] == "critical-cyber" for w in wls)
    assert config.find_watchlist("ai-x-cyber") is not None
    assert config.find_watchlist("nope") is None


# ---- alert bookkeeping ----
def test_alert_baseline_and_known_ids(store):
    assert not store.alert_baseline_exists("wl")
    store.record_alert_hits("wl", ["a", "b"])
    assert store.alert_baseline_exists("wl")
    assert store.known_alert_ids("wl") == {"a", "b"}


# ---- alert engine end-to-end (log channel) ----
class _FakeSvc:
    def __init__(self, store):
        self.store = store
        self.settings = {"alerts": {"enabled": True, "channel": "log", "max_per_watchlist": 5}}

    def watchlists(self):
        return [{"name": "rw", "label": "Ransomware", "alert": True,
                 "filters": {"q": "ransomware"}}]


def test_alert_baseline_then_notify(store, capsys):
    store.upsert_many([art("LockBit ransomware hits bank")])
    eng = AlertEngine(_FakeSvc(store))

    assert eng.check_and_notify(verbose=True) == 0          # baseline, silent
    store.upsert_many([art("Akira ransomware new campaign")])
    assert eng.check_and_notify() == 1                       # one new match
    assert eng.check_and_notify() == 0                       # nothing new now


def test_alerts_disabled_is_noop(store):
    svc = _FakeSvc(store)
    svc.settings["alerts"]["enabled"] = False
    store.upsert_many([art("some ransomware thing")])
    assert AlertEngine(svc).check_and_notify() == 0


def test_alert_ignores_duplicates(store):
    a = art("ransomware A", source="X")
    b = art("ransomware A copy", source="Y")
    b.duplicate_of = a.id
    store.upsert_many([a, b])
    eng = AlertEngine(_FakeSvc(store))
    eng.check_and_notify()                                   # baseline (only non-dup)
    assert b.id not in store.known_alert_ids("rw")           # dup never baselined
