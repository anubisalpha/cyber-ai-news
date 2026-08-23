"""Tests for the refresh pipeline and scheduler guards (no real network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, service  # noqa: E402
from src.models import Article  # noqa: E402


def _isolated_settings(tmp_path):
    s = config.load_settings()
    s["storage"] = dict(s.get("storage", {}))
    s["storage"]["db_file"] = str(tmp_path / "t.db")  # absolute -> ROOT/abs == abs
    s["storage"]["data_file"] = str(tmp_path / "none.json")  # no legacy migration
    s.setdefault("schedule", {})["enabled"] = False
    return s


def test_refresh_pipeline_classifies_and_records_health(tmp_path, monkeypatch):
    settings = _isolated_settings(tmp_path)
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setattr(config, "load_sources",
                        lambda enabled_only=True: [
                            {"name": "F", "type": "rss", "domain": "cyber", "weight": 3}])

    def fake_fetch(src, s):
        return [
            Article(title="LockBit ransomware hits hospital", url="u1", source="F", domain="cyber"),
            Article(title="CVE-2026-1 critical RCE flaw", url="u2", source="F", domain="cyber"),
        ]
    monkeypatch.setattr(service, "fetch_source", fake_fetch)

    svc = service.NewsService()
    total = svc.refresh(verbose=False)
    assert total == 2

    items = svc.store.all()
    assert any("malware" in c for a in items for c in a.categories)       # classified
    assert any("vulnerabilities" in c for a in items for c in a.categories)
    assert svc.store.health()[0]["ok"] == 1                               # health recorded


def test_refresh_survives_a_failing_source(tmp_path, monkeypatch):
    settings = _isolated_settings(tmp_path)
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setattr(config, "load_sources",
                        lambda enabled_only=True: [
                            {"name": "Bad", "type": "rss", "domain": "cyber"},
                            {"name": "Good", "type": "rss", "domain": "ai"}])

    def fake_fetch(src, s):
        if src["name"] == "Bad":
            raise RuntimeError("feed exploded")
        return [Article(title="new model released", url="g1", source="Good", domain="ai")]
    monkeypatch.setattr(service, "fetch_source", fake_fetch)

    svc = service.NewsService()
    assert svc.refresh(verbose=False) == 1                                # good source still stored
    health = {h["name"]: h for h in svc.store.health()}
    assert health["Bad"]["ok"] == 0 and health["Bad"]["fail_streak"] == 1
    assert health["Good"]["ok"] == 1


def test_scheduler_disabled_by_default():
    from src import scheduler
    # real settings ship schedule.enabled=false
    assert scheduler.start_background() is False


def test_scheduler_safe_refresh_swallows_errors(monkeypatch, capsys):
    from src import scheduler
    monkeypatch.setattr(scheduler, "NewsService",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    scheduler._safe_refresh(verbose=True)          # must not raise
    assert "refresh failed" in capsys.readouterr().out
