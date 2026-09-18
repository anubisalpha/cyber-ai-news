"""Fetch articles from RSS feeds and JSON APIs.

RSS is handled generically via feedparser. JSON APIs need per-source parsers
(the `parser` key in sources.yaml maps to a function in JSON_PARSERS).
Adding a new JSON source = add a parser here + an entry in the config.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import feedparser
import requests

from .models import Article


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------
def fetch_rss(source: dict, settings: dict) -> list[Article]:
    fcfg = settings.get("fetch", {})
    max_items = fcfg.get("max_items_per_source", 50)

    parsed = feedparser.parse(
        source["url"],
        request_headers={"User-Agent": fcfg.get("user_agent", "cyber-ai-news")},
    )

    articles: list[Article] = []
    for entry in parsed.entries[:max_items]:
        published = _entry_date(entry)
        articles.append(
            Article(
                title=(entry.get("title") or "").strip(),
                url=entry.get("link") or "",
                source=source["name"],
                domain=source.get("domain", "cyber"),
                summary=_clean(entry.get("summary", "")),
                published=published,
                weight=source.get("weight", 3),
            )
        )
    return articles


def _entry_date(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
    return None


def _clean(html: str) -> str:
    """Strip tags crudely; good enough for keyword matching + preview."""
    import re
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()[:1000]


# ---------------------------------------------------------------------------
# JSON APIs
# ---------------------------------------------------------------------------
def fetch_json(source: dict, settings: dict) -> list[Article]:
    fcfg = settings.get("fetch", {})
    parser_name = source.get("parser")
    parser = JSON_PARSERS.get(parser_name)
    if parser is None:
        raise ValueError(
            f"Source '{source['name']}' is json_api but has no known parser "
            f"(parser={parser_name!r}). Add one to JSON_PARSERS in fetch.py."
        )
    resp = requests.get(
        source["url"],
        timeout=fcfg.get("timeout_seconds", 20),
        headers={"User-Agent": fcfg.get("user_agent", "cyber-ai-news")},
    )
    resp.raise_for_status()
    return parser(resp.json(), source, fcfg)


def _parse_cisa_kev(data: dict, source: dict, fcfg: dict) -> list[Article]:
    out: list[Article] = []
    for v in data.get("vulnerabilities", [])[: fcfg.get("max_items_per_source", 50)]:
        cve = v.get("cveID", "")
        title = f"{cve}: {v.get('vulnerabilityName', '')}".strip(": ")
        out.append(
            Article(
                title=title,
                url=f"https://nvd.nist.gov/vuln/detail/{cve}",
                source=source["name"],
                domain="cyber",
                summary=v.get("shortDescription", ""),
                published=_kev_date(v.get("dateAdded")),
                weight=source.get("weight", 5),
            )
        )
    return out


def validate_feed_url(url: str, settings: dict) -> dict:
    """Attempt to parse a URL as RSS/Atom. Returns ok/title/item_count or ok/error."""
    try:
        fcfg = settings.get("fetch", {})
        parsed = feedparser.parse(
            url,
            request_headers={"User-Agent": fcfg.get("user_agent", "cyber-ai-news")},
        )
        # bozo flag means the parser hit an error; reject if there are also no entries
        if parsed.get("bozo") and not parsed.entries:
            exc = parsed.get("bozo_exception")
            return {"ok": False, "error": f"Not a valid RSS/Atom feed: {exc}"}
        return {
            "ok": True,
            "title": (parsed.feed.get("title") or "").strip(),
            "item_count": len(parsed.entries),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _kev_date(d: str | None) -> str | None:
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _parse_nvd_cve(data: dict, source: dict, fcfg: dict) -> list[Article]:
    out: list[Article] = []
    for item in data.get("vulnerabilities", [])[: fcfg.get("max_items_per_source", 50)]:
        cve = item.get("cve", {})
        cve_id = cve.get("id", "")
        descs = cve.get("descriptions", [])
        summary = next((d["value"] for d in descs if d.get("lang") == "en"), "")
        out.append(
            Article(
                title=f"{cve_id}",
                url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                source=source["name"],
                domain="cyber",
                summary=summary,
                published=cve.get("published"),
                weight=source.get("weight", 5),
            )
        )
    return out


JSON_PARSERS: dict[str, Callable[[dict, dict, dict], list[Article]]] = {
    "cisa_kev": _parse_cisa_kev,
    "nvd_cve": _parse_nvd_cve,
}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def fetch_source(source: dict, settings: dict) -> list[Article]:
    stype = source.get("type", "rss")
    if stype == "rss":
        return fetch_rss(source, settings)
    if stype == "json_api":
        return fetch_json(source, settings)
    raise ValueError(f"Unknown source type: {stype}")
