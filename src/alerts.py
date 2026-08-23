"""Keyword / watchlist alerts.

After a refresh, checks each alert-enabled watchlist for NEW matching items and
notifies once per item. To avoid emailing the whole backlog the first time a
watchlist becomes an alert, the first check silently establishes a baseline
(records current matches as already-seen) and sends nothing.

Config (settings.yaml):
  alerts:
    enabled: false
    channel: email          # email | log
    recipient: null         # null -> claude-mail DEFAULT_TO
    max_per_watchlist: 10

Enabling `channel: email` sends REAL email autonomously on each refresh that
finds new matches — opt in deliberately.
"""
from __future__ import annotations

from datetime import datetime
from html import escape


class AlertEngine:
    def __init__(self, service):
        self.svc = service
        self.cfg = service.settings.get("alerts", {}) or {}

    def check_and_notify(self, verbose: bool = False) -> int:
        if not self.cfg.get("enabled", False):
            return 0

        store = self.svc.store
        cap = self.cfg.get("max_per_watchlist", 10)
        hits: list[tuple[str, list]] = []  # (label, [articles])
        total_new = 0

        for wl in self.svc.watchlists():
            if not wl["alert"]:
                continue
            name, label = wl["name"], wl["label"]
            items, _ = self.svc.store.query(limit=1000, **wl["filters"])
            items = [a for a in items if not a.duplicate_of]
            ids = [a.id for a in items]

            if not store.alert_baseline_exists(name):
                store.record_alert_hits(name, ids)  # silent baseline
                if verbose:
                    print(f"  [alert] baseline set for '{name}' ({len(ids)} items)")
                continue

            known = store.known_alert_ids(name)
            new_items = [a for a in items if a.id not in known]
            if new_items:
                store.record_alert_hits(name, [a.id for a in new_items])
                hits.append((label, new_items[:cap]))
                total_new += len(new_items)

        if hits:
            self._notify(hits, total_new, verbose)
        return total_new

    # ---- notification ----------------------------------------------------
    def _notify(self, hits, total_new, verbose):
        channel = self.cfg.get("channel", "log")
        if channel == "email":
            try:
                self._email(hits, total_new)
                if verbose:
                    print(f"  [alert] emailed {total_new} new item(s)")
            except Exception as exc:  # noqa: BLE001
                print(f"  [alert] email failed: {exc}")
        else:
            print(f"[alert] {total_new} new matching item(s):")
            for label, items in hits:
                for a in items:
                    print(f"   ({label}) {a.title}")

    def _email(self, hits, total_new):
        subject = f"Cyber+AI Alert — {total_new} new item{'s' if total_new != 1 else ''}"
        blocks = []
        for label, items in hits:
            rows = "".join(
                f'<div style="margin:0 0 8px"><a href="{escape(a.url)}" '
                f'style="color:#0969da;text-decoration:none;font-weight:600">'
                f'{escape(a.title)}</a><br>'
                f'<span style="font-size:12px;color:#57606a">{escape(a.source)} · '
                f'{escape(a.domain)}{" · " + a.severity if a.severity else ""}</span></div>'
                for a in items
            )
            blocks.append(
                f'<h2 style="font-size:15px;margin:18px 0 8px">{escape(label)} '
                f'<span style="color:#57606a;font-weight:400">({len(items)})</span></h2>{rows}'
            )
        html = (
            '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
            'max-width:640px;margin:0 auto;padding:20px;color:#111">'
            '<h1 style="font-size:19px;margin:0 0 6px">⚠ Watchlist alerts</h1>'
            f'<div style="font-size:12px;color:#57606a">{datetime.now():%d %b %Y %H:%M}</div>'
            + "".join(blocks)
            + '<div style="margin-top:20px;font-size:11px;color:#8b949e">'
            'cyber-ai-news · github.com/anubisalpha/cyber-ai-news</div></div>'
        )
        from .mailer import send_email  # noqa: PLC0415
        send_email(self.cfg.get("recipient"), subject, html)
