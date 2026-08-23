"""Near-duplicate detection: cluster the same story across multiple outlets.

Approach: normalise each title to a set of meaningful tokens, then greedily
group titles whose Jaccard similarity exceeds a threshold. The earliest-published
article in a group is the canonical one; the rest get `duplicate_of` set to it.
All members share a `cluster_id`.

Guards against false merges from templated titles:
  - cross_source_only: only merge items from DIFFERENT outlets (a single source
    emitting near-identical titles — e.g. CISA "Adds Two/Three/Four KEV to
    Catalog", or a monthly roundup — is NOT a duplicate).
  - max_days_apart: real cross-outlet coverage clusters in time.

O(n * clusters) — fine for the current scale. If the corpus grows into the tens
of thousands, add date-window blocking before comparing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import Article

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "as", "is", "are", "was", "were", "be", "new", "how", "why",
    "what", "that", "this", "its", "it", "into", "over", "after", "amid", "via",
    "you", "your", "can", "will", "has", "have", "not", "no", "now",
}


def _tokens(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return frozenset(w for w in words if len(w) > 2 and w not in _STOPWORDS)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _parse_dt(iso: str | None):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def cluster_duplicates(
    articles: list[Article],
    threshold: float = 0.7,
    cross_source_only: bool = True,
    max_days_apart: int = 3,
) -> list[Article]:
    """Assign cluster_id / duplicate_of in place.

    Returns the article list if anything changed (so the caller can persist),
    otherwise an empty list.
    """
    ordered = sorted(articles, key=lambda a: a.published or "")
    # cluster = (canonical_article, tokens, set_of_member_sources)
    clusters: list[tuple[Article, frozenset, set]] = []
    changed = False

    for a in ordered:
        toks = _tokens(a.title)
        a_dt = _parse_dt(a.published)
        best, best_sim = None, 0.0

        for canon, ctoks, sources in clusters:
            if cross_source_only and a.source in sources:
                continue
            if max_days_apart and a_dt:
                c_dt = _parse_dt(canon.published)
                if c_dt and abs((a_dt - c_dt).days) > max_days_apart:
                    continue
            sim = _jaccard(toks, ctoks)
            if sim > best_sim:
                best_sim, best = sim, (canon, sources)

        if best is not None and toks and best_sim >= threshold:
            canon, sources = best
            sources.add(a.source)
            new_cluster = canon.cluster_id or canon.id
            new_dup = canon.id
        else:
            clusters.append((a, toks, {a.source}))
            new_cluster = a.id
            new_dup = None

        if a.cluster_id != new_cluster or a.duplicate_of != new_dup:
            changed = True
        a.cluster_id = new_cluster
        a.duplicate_of = new_dup

    return articles if changed else []
