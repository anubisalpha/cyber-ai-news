# Enhancement Plan

Status legend: ⬜ todo · 🔄 in progress · ✅ done

## Audit findings (2026-08-23, at v0.2)

- **~30% of articles uncategorized** (112 / 377). Keyword rules miss too much. → Phase 1.
- **JSON storage won't scale** with unlimited history: every refresh loads + rewrites
  the whole file. Fine at hundreds, slow at tens of thousands. → Phase 1 (SQLite).

## Phase 1 — Quality & foundation  *(chosen first)*

- ✅ **SQLite storage backend** (`src/storage.py`)
  - SQLite DB at `data/news.db`; filtering + pagination in SQL; upsert preserves
    `first_seen`; indexes on published/domain/severity/first_seen.
  - `import_json` / `export_json` for portability; migrates the legacy
    `data/articles.json` on first run.
- ✅ **Classification improvements**
  - Expanded keyword rules (extensions, web3/wallet, card fraud, OT/ICS,
    post-quantum, agentic AI, OWASP, surveillance, …). **Uncategorised 30% → 21%.**
  - **LLM fallback** (`src/llm_classify.py`): optional pass over `uncategorized`
    items, google-generativeai + GOOGLE_API_KEY (gemini flash). Batched, graceful
    no-op if key/lib missing. Toggle `classify.llm_fallback.{enabled,model,batch_size,max_items}`.
    ✅ live-validated (gemini-3.6-flash): rescued 9/12 sampled uncategorized items
    with sensible tags; off by default. NOTE: the `google.generativeai` SDK is
    deprecated upstream — migrate to `google.genai` later.
- ✅ **Near-duplicate detection** (`src/dedupe.py`) — token-set Jaccard,
  cross-source-only + time-window guards to avoid templated-title false merges;
  sets `cluster_id` / `duplicate_of`.
- ✅ **Tests + pagination** — 28 tests (classify/dedupe/storage/llm/api);
  `/api/articles` now returns `total/offset/limit` + `include_duplicates`.

## Phase 2 — Delivery & automation  *(done)*

- ✅ **Overview dashboard** (primary landing page) — `/api/overview` + two-view web
  UI (Overview + Browse), KPI tiles, severity/domain/category/source breakdowns,
  clickable through to filtered Browse.
- ✅ **Email digest** via `claude-mail` (`src/digest.py`, `cli digest [--send]`) —
  config-driven sections in `settings.yaml`; previews to HTML, `--send` emails.
- ✅ **Scheduled auto-refresh** (`src/scheduler.py`) — in-process thread on API
  startup when `schedule.enabled`, or standalone `python -m src.scheduler`
  (schtasks is blocked on this box). Optional autonomous daily digest (opt-in).
- ✅ **RSS/Atom output** — `/feed.xml` filtered RSS 2.0 (round-trips through feedparser).
- ✅ **Per-source health monitoring** — `source_health` table, recorded each refresh;
  `cli health`, `/api/sources/health`, and a dashboard header badge.

  SQLite hardened for concurrency (WAL + busy_timeout) since scheduler + API share the DB.

## Phase 3 — Personalization & UX  *(done)*

- ✅ **Saved watchlists** — `config/watchlists.yaml` named filters; `/api/watchlists`,
  `/api/watchlists/{name}`, `/feed.xml?watchlist=`, `cli watchlists` / `list --watchlist`;
  dashboard watchlist strip (with 🔔 on alert-enabled lists).
- ✅ **Keyword alerts** (`src/alerts.py`) — new items matching an alert watchlist
  notify once (email via claude-mail or log). Silent baseline on first check so
  enabling doesn't blast the backlog. Off by default (`alerts.enabled`).
- ✅ **Dashboard polish** — watchlist strip, relative timestamps (full date on hover),
  read state (dims visited), bookmark stars + "Saved only" filter (localStorage),
  load-more pagination.
- ✅ **Dockerize** — `Dockerfile` (python-slim, non-root, healthcheck) + `compose.yml`
  (named volume for SQLite history). Built + run-tested (fetched 377 in-container).
  Optional LLM dep split into `requirements-llm.txt` to keep the image lean (237MB).

## Done: Phases 1–3 all complete. Possible Phase 4 ideas

- Multi-user / auth if hosted publicly; per-user watchlists & bookmarks (server-side).
- Trend charts over time (history is now retained, so time-series is possible).
- More sources + JSON parsers; full-text article fetch/enrichment.
- Migrate `google.generativeai` → `google.genai` (SDK deprecation).

## Decisions locked in

- Classification: **keywords + LLM fallback** (LLM only on uncategorized, to control cost).
- Storage: **SQLite**, JSON kept as import/export format.
