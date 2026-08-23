"""Scheduled auto-refresh (and optional daily digest).

Two ways to run:
  - In-process: the API starts this in a background thread when
    `schedule.enabled` is true (see api.py).
  - Standalone: `python -m src.scheduler` runs the same loop in the foreground,
    e.g. from a Startup-folder script (schtasks is blocked on this machine).

Config (settings.yaml):
  schedule:
    enabled: false
    interval_minutes: 60
    refresh_on_start: false
    digest_daily_at: null     # "08:00" (local 24h) sends the digest daily; null = off
                              # NOTE: this sends REAL email autonomously — opt in explicitly.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from . import config
from .service import NewsService

_thread: threading.Thread | None = None
_stop = threading.Event()


def _run(stop: threading.Event, verbose: bool = True) -> None:
    sc = config.load_settings().get("schedule", {}) or {}
    interval = max(1, int(sc.get("interval_minutes", 60))) * 60
    digest_at = sc.get("digest_daily_at")
    last_digest_day = None

    if sc.get("refresh_on_start"):
        _safe_refresh(verbose)
    next_refresh = time.time() + interval

    while not stop.is_set():
        now = time.time()
        if now >= next_refresh:
            _safe_refresh(verbose)
            next_refresh = time.time() + interval
        if digest_at:
            n = datetime.now()
            if n.strftime("%H:%M") == str(digest_at) and last_digest_day != n.date():
                _safe_digest(verbose)
                last_digest_day = n.date()
        stop.wait(20)  # responsive shutdown; re-checks the clock every 20s


def _safe_refresh(verbose: bool) -> None:
    try:
        total = NewsService().refresh(verbose=False)
        if verbose:
            print(f"[scheduler] refreshed — {total} articles at {datetime.now():%H:%M:%S}")
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] refresh failed: {exc}")


def _safe_digest(verbose: bool) -> None:
    try:
        from .digest import DigestBuilder  # noqa: PLC0415
        recipient = DigestBuilder(NewsService()).send()
        if verbose:
            print(f"[scheduler] digest emailed to {recipient}")
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] digest failed: {exc}")


def start_background() -> bool:
    """Start the scheduler in a daemon thread. Returns True if started."""
    global _thread
    if not (config.load_settings().get("schedule", {}) or {}).get("enabled", False):
        return False
    if _thread and _thread.is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_run, args=(_stop, False), daemon=True)
    _thread.start()
    return True


def stop_background() -> None:
    _stop.set()


def run_foreground() -> None:
    print("Scheduler running (Ctrl+C to stop).")
    try:
        _run(threading.Event(), verbose=True)
    except KeyboardInterrupt:
        print("\nScheduler stopped.")


if __name__ == "__main__":
    run_foreground()
