"""Scheduled auto-refresh (and optional daily digest).

Two ways to run:
  - In-process: the API starts this in a background thread when
    `schedule.enabled` is true (see api.py).
  - Standalone: `python -m src.scheduler` runs the same loop in the foreground,
    e.g. from a Startup-folder script (schtasks is blocked on this machine).

Config (settings.yaml):
  schedule:
    enabled: false
    refresh_on_start: false

    working_hours:
      enabled: true
      days: [mon, tue, wed, thu, fri]
      from: "08:00"
      to: "18:00"

    nightly_sweep:
      enabled: true
      at: "02:00"

    critical_digest:
      enabled: false     # requires Phase 4 user subscriptions; uses digest.recipient until then
                         # NOTE: enabling this sends REAL email autonomously — opt in explicitly.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from . import config
from .service import NewsService

_thread: threading.Thread | None = None
_stop = threading.Event()

_DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_days(days: list) -> set:
    return {_DAY_MAP[d.lower()[:3]] for d in days if d.lower()[:3] in _DAY_MAP}


def _run(stop: threading.Event, verbose: bool = True) -> None:
    sc = config.load_settings().get("schedule", {}) or {}
    wh = sc.get("working_hours", {}) or {}
    ns = sc.get("nightly_sweep", {}) or {}
    cd = sc.get("critical_digest", {}) or {}

    last_nightly_day = None
    last_hourly: datetime | None = None

    if sc.get("refresh_on_start"):
        _safe_refresh(verbose)

    while not stop.is_set():
        now = datetime.now()

        # Nightly sweep at configured time, every day.
        if ns.get("enabled"):
            nh, nm = map(int, (ns.get("at", "02:00")).split(":"))
            if now.hour == nh and now.minute < 1 and last_nightly_day != now.date():
                last_nightly_day = now.date()
                anchor = now.replace(second=0, microsecond=0)
                _safe_refresh(verbose)
                _safe_digest(verbose)
                if cd.get("enabled"):
                    _safe_critical_digest(anchor, verbose)

        # Working-hours hourly refresh (Mon–Fri, fires on the hour, within window only).
        if wh.get("enabled"):
            allowed = _parse_days(wh.get("days", ["mon", "tue", "wed", "thu", "fri"]))
            fh, fm = map(int, (wh.get("from", "08:00")).split(":"))
            th, tm = map(int, (wh.get("to", "18:00")).split(":"))
            in_window = (
                now.weekday() in allowed
                and (now.hour > fh or (now.hour == fh and now.minute >= fm))
                and (now.hour < th or (now.hour == th and now.minute <= tm))
            )
            this_hour = now.replace(minute=0, second=0, microsecond=0)
            if in_window and now.minute < 1 and last_hourly != this_hour:
                last_hourly = this_hour
                _safe_refresh(verbose)

        stop.wait(30)  # responsive shutdown; re-checks the clock every 30s


def _safe_refresh(verbose: bool) -> None:
    try:
        total = NewsService().refresh(verbose=False)
        if verbose:
            print(f"[scheduler] refreshed — {total} articles at {datetime.now():%H:%M:%S}")
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] refresh failed: {exc}")


def _safe_critical_digest(anchor: datetime, verbose: bool) -> None:
    try:
        from .digest import DigestBuilder  # noqa: PLC0415
        recipient = DigestBuilder(NewsService()).send_critical(anchor_time=anchor)
        if verbose:
            print(f"[scheduler] critical digest emailed to {recipient}")
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] critical digest failed: {exc}")


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
