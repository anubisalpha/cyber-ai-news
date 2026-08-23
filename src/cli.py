"""Command-line interface.

Usage:
    python -m src.cli refresh
    python -m src.cli list --domain cyber --severity critical --limit 10
    python -m src.cli list --category vulnerabilities
    python -m src.cli categories
    python -m src.cli sources
"""
from __future__ import annotations

import argparse
import sys

from . import config
from .service import NewsService


def _print_article(a) -> None:
    sev = f" [{a.severity.upper()}]" if a.severity else ""
    cats = ", ".join(a.categories)
    date = (a.published or "")[:10]
    print(f"\n{a.title}{sev}")
    print(f"  {a.source} · {date} · {a.domain}")
    print(f"  {a.url}")
    print(f"  tags: {cats}")


def cmd_refresh(args) -> int:
    svc = NewsService()
    print("Refreshing sources...")
    svc.refresh(verbose=True)
    return 0


def cmd_list(args) -> int:
    svc = NewsService()
    items = svc.query(
        domain=args.domain,
        category=args.category,
        severity=args.severity,
        region=args.region,
        source=args.source,
        limit=args.limit,
        sort=args.sort,
    )
    if not items:
        print("No matching articles. Run `refresh` first, or loosen filters.")
        return 0
    for a in items:
        _print_article(a)
    print(f"\n{len(items)} article(s).")
    return 0


def cmd_digest(args) -> int:
    from .digest import DigestBuilder
    builder = DigestBuilder(NewsService())
    if args.send:
        recipient = builder.send(to=args.to)
        print(f"Digest emailed to {recipient}.")
        return 0
    # preview mode (default): write HTML to a file, don't email
    subject, html, total = builder.build()
    out = args.out or "digest_preview.html"
    from pathlib import Path
    Path(out).write_text(html, encoding="utf-8")
    print(f"Subject: {subject}")
    print(f"{total} items. Preview written to {out} (use --send to email it).")
    return 0


def cmd_categories(args) -> int:
    cfg = config.load_categories()
    for domain, cats in cfg.get("categories", {}).items():
        print(f"\n== {domain} ==")
        for key, spec in cats.items():
            print(f"  {domain}.{key:20s} {spec.get('label', '')}")
    return 0


def cmd_sources(args) -> int:
    for s in config.load_sources(enabled_only=False):
        flag = "on " if s.get("enabled", True) else "off"
        print(f"  [{flag}] {s['type']:8s} {s['domain']:6s} {s['name']}")
    return 0


def cmd_health(args) -> int:
    h = NewsService().source_health()
    if not h["sources"]:
        print("No health data yet — run `refresh` first.")
        return 0
    icon = {"ok": "[ ok ]", "empty": "[empty]", "failing": "[FAIL]"}
    for r in h["sources"]:
        checked = (r.get("last_checked") or "")[:19].replace("T", " ")
        extra = f" streak={r['fail_streak']}" if r["fail_streak"] else ""
        print(f"  {icon[r['status']]} {r['name']:42s} {r['last_count'] or 0:>3} items  {checked}{extra}")
    print(f"\n{h['problem_count']} problem source(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cyber-ai-news", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("refresh", help="fetch + classify + store").set_defaults(func=cmd_refresh)

    lst = sub.add_parser("list", help="query stored articles")
    lst.add_argument("--domain", choices=["cyber", "ai"], help="filter by domain")
    lst.add_argument("--category", help="substring match on category tag, e.g. vulnerabilities")
    lst.add_argument("--severity", choices=["critical", "high", "medium", "low"])
    lst.add_argument("--region", choices=["us", "eu", "uk", "apac"])
    lst.add_argument("--source", help="substring match on source name")
    lst.add_argument("--limit", type=int, default=None)
    lst.add_argument("--sort", choices=["date", "weight"], default=None)
    lst.set_defaults(func=cmd_list)

    dg = sub.add_parser("digest", help="build (and optionally email) a news digest")
    dg.add_argument("--send", action="store_true", help="actually email it (default: preview only)")
    dg.add_argument("--to", help="override recipient")
    dg.add_argument("--out", help="preview output file (default: digest_preview.html)")
    dg.set_defaults(func=cmd_digest)

    sub.add_parser("categories", help="list the category taxonomy").set_defaults(func=cmd_categories)
    sub.add_parser("sources", help="list configured sources").set_defaults(func=cmd_sources)
    sub.add_parser("health", help="show per-source fetch health").set_defaults(func=cmd_health)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
