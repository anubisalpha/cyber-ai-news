# cyber-ai-news

A small, config-driven service that fetches the latest **cybersecurity** and **AI**
news from RSS feeds and JSON APIs, auto-tags each item into a filterable
taxonomy, and lets you query it.

Everything you'll want to tune — sources, categories, keywords, filters — lives
in **`config/`** as editable lists. No feed or category is hard-coded in the code.

## What it does

```
fetch (RSS + JSON APIs) -> classify (keyword rules + optional LLM fallback)
                        -> near-duplicate clustering -> store (SQLite) -> query/filter
                                                                          |
                                                        CLI  ·  REST API  ·  web dashboard
```

## Layout

| Path | Purpose |
|---|---|
| `config/sources.yaml` | The source list — RSS feeds & JSON APIs, each toggleable |
| `config/categories.yaml` | The category taxonomy + keyword rules for auto-tagging |
| `config/settings.yaml` | Runtime settings (timeouts, age filter, sort defaults) |
| `src/` | Fetch, classify, LLM fallback, dedupe, storage, orchestrate, CLI, REST API |
| `web/index.html` | Single-file web dashboard (served by the API) |
| `tests/` | Tests: classify, dedupe, storage, llm, api (28) |
| `data/news.db` | SQLite history of fetched+tagged articles (git-ignored) |
| `PLAN.md` | Enhancement roadmap + status |

## Setup

```bash
cd projects/cyber-ai-news
python -m venv .venv
.venv/Scripts/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

```bash
# Fetch everything from enabled sources, classify, and cache it
python -m src.cli refresh

# List the latest, with filters
python -m src.cli list --domain cyber --severity critical --limit 10
python -m src.cli list --category vulnerabilities
python -m src.cli list --domain ai --category model_releases
python -m src.cli list --region uk

# Inspect config
python -m src.cli categories     # show the taxonomy
python -m src.cli sources        # show configured feeds (on/off)
```

## REST API + web dashboard

```bash
uvicorn src.api:app --reload --port 8000
```

Then open **http://127.0.0.1:8000/** for the dashboard — filter by domain,
category, severity, region, free-text search, and trigger a live refresh from
the header button.

API endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/articles` | Filtered, paginated list. Params: `domain, category, severity, region, source, q, sort, limit, offset, include_duplicates` (returns `total/offset/limit`) |
| GET | `/api/categories` | The taxonomy |
| GET | `/api/sources` | Configured sources |
| GET | `/api/stats` | Aggregate counts (by domain / severity / category / source) |
| POST | `/api/refresh` | Fetch + classify in the background |
| GET | `/api/health` | Liveness + last-refresh status |

Interactive API docs are auto-generated at `/docs`.

## History & retention

Every refresh **accumulates** into the local history (`data/articles.json`),
de-duplicated by URL. Each article carries a `first_seen` timestamp.

- `fetch.max_age_days: 0` — ingest whatever the feeds return (no publish-date cutoff).
- `storage.history_retention_days: 0` — **unlimited history** (default). Set to a
  number of days to trim items first seen longer ago than that on each refresh
  (e.g. `90` keeps ~3 months).

## The taxonomy (filterable news types)

**Cybersecurity:** vulnerabilities/CVEs · active exploitation · breaches & leaks ·
threat actors/APTs · malware & ransomware · patches & advisories · supply chain ·
cloud & infrastructure · identity & access · regulation & compliance ·
policy & geopolitics · tools & defense · industry & business · research & conferences

**AI:** model releases · research & papers · safety & alignment · AI security ·
regulation & governance · industry & business · tooling & infrastructure ·
applications · ethics & society · open source

**Cross-cutting filters** (orthogonal): severity · region · content type.

**Intersection:** a dedicated AI × Cyber bucket (prompt injection, deepfake fraud,
AI-powered attacks, LLM security, etc.).

## Classification (keywords + optional LLM fallback)

Tagging is **rule-based** (fast, free, deterministic) via the keyword lists in
`categories.yaml`. Items the rules can't place are marked `uncategorized`.

An **optional LLM fallback** re-tags only those uncategorized items using Gemini,
so it costs nothing on the common path. It's **off by default**:

```yaml
# config/settings.yaml
classify:
  llm_fallback:
    enabled: true            # turn it on
    model: gemini-2.0-flash
    batch_size: 10
    max_items: 40            # cap per refresh to bound cost
```

Requires `pip install google-generativeai` and `GOOGLE_API_KEY` (read from the
environment or the claudecore `.env`). If either is missing it's a silent no-op.

## Storage & deduplication

- **SQLite** (`data/news.db`) — filtering and pagination run in SQL, so unlimited
  history scales. The legacy `data/articles.json` is imported once on first run.
- **Near-duplicate clustering** groups the same story across outlets
  (`cluster_id` / `duplicate_of`). Guards against templated-title false merges by
  only merging across *different* sources within a time window (see `dedupe:` in
  settings). Hide dupes via `/api/articles?include_duplicates=false`.

## Extending

- **Add a source:** append an entry to `config/sources.yaml`. RSS works out of the
  box; a JSON API also needs a small parser in `src/fetch.py` (`JSON_PARSERS`).
- **Add/tune a category:** edit `config/categories.yaml` — add keywords or a new
  category block. No code change needed.

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## Roadmap ideas

- ~~REST API (FastAPI) + web dashboard~~ ✅ done
- Email / RSS digest output
- Optional LLM classifier pass for ambiguous items
- Scheduled refresh (cron / Task Scheduler)
- Per-source health monitoring
