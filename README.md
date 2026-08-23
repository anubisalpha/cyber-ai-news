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
| `src/` | Fetch, classify, LLM fallback, dedupe, storage, digest, scheduler, alerts, CLI, REST API |
| `config/watchlists.yaml` | Named saved filters (watchlists) |
| `web/index.html` | Single-file web dashboard (Overview + Browse), served by the API |
| `tests/` | Tests: classify, dedupe, storage, llm, api, phase2, phase3 (38) |
| `Dockerfile`, `compose.yml` | Container packaging |
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

# Inspect config / status
python -m src.cli categories     # show the taxonomy
python -m src.cli sources        # show configured feeds (on/off)
python -m src.cli health         # per-source fetch health
python -m src.cli watchlists     # saved watchlists with counts
python -m src.cli digest         # build a digest preview (--send to email)
```

## REST API + web dashboard

```bash
uvicorn src.api:app --reload --port 8000
```

Then open **http://127.0.0.1:8000/**. The dashboard has two views:

- **Overview** (landing) — KPI tiles (total, new-this-week, critical, AI×Cyber),
  latest highlights, severity/domain breakdowns, top categories & sources, plus
  "critical" and "AI×Cyber" panels. Everything clicks through to a filtered Browse.
- **Browse** — the filterable article list (domain, category, severity, region,
  free-text search, sort, load-more pagination).

The header shows a **source-health badge** and a live **Refresh feeds** button.

API endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/overview` | One-call summary for the dashboard (KPIs, breakdowns, highlights) |
| GET | `/api/articles` | Filtered, paginated list. Params: `domain, category, severity, region, source, q, sort, limit, offset, include_duplicates` (returns `total/offset/limit`) |
| GET | `/api/categories` | The taxonomy |
| GET | `/api/sources` | Configured sources |
| GET | `/api/sources/health` | Per-source fetch health (ok / empty / failing) |
| GET | `/api/watchlists` | Saved watchlists with match counts |
| GET | `/api/watchlists/{name}` | Articles for a named watchlist (paginated) |
| GET | `/api/stats` | Aggregate counts (by domain / severity / category / source) |
| GET | `/feed.xml` | Filtered **RSS 2.0** output feed (same filters as `/api/articles`) |
| POST | `/api/refresh` | Fetch + classify in the background |
| GET | `/api/health` | Liveness + last-refresh status |

Interactive API docs are auto-generated at `/docs`.

## Delivery & automation

**Email digest** — a configurable HTML summary sent via the `claude-mail` project:

```bash
python -m src.cli digest              # preview to digest_preview.html (no email)
python -m src.cli digest --send       # actually email it (to claude-mail DEFAULT_TO)
python -m src.cli digest --send --to me@example.com
```

Sections, window, and recipient are config-driven under `digest:` in settings.

**Scheduled auto-refresh** — keep the DB fresh automatically:

```bash
python -m src.scheduler               # standalone loop (Ctrl+C to stop)
```

Or set `schedule.enabled: true` and the API starts it in a background thread.
`schedule.digest_daily_at: "08:00"` also emails the digest once a day (opt-in —
it sends real email autonomously). `schtasks` is blocked on this machine, so use
the standalone runner from a Startup-folder script if you want it outside the API.

**Source health** — every refresh records per-source success/count/errors:

```bash
python -m src.cli health
```

Sources show as `ok`, `empty` (0 items — e.g. arXiv on weekends), or `failing`
(with a fail streak). The dashboard surfaces a ⚠ badge when any need attention.

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

## Watchlists & alerts

**Watchlists** are named, reusable filters defined in `config/watchlists.yaml` —
they power the dashboard's watchlist strip, per-list RSS, and alerts.

```bash
python -m src.cli watchlists                 # list them with live counts
python -m src.cli list --watchlist ai-x-cyber
# per-watchlist RSS:  /feed.xml?watchlist=critical-cyber
```

**Alerts** notify you when *new* items match an alert-enabled watchlist (those with
`alert: true`). Checked after every refresh. Off by default; enable in settings:

```yaml
# config/settings.yaml
alerts:
  enabled: true
  channel: email        # or "log"
```

The first check after enabling silently establishes a baseline (records current
matches), so you only get alerted about items that appear *afterwards* — never the
whole backlog. `channel: email` sends real email via claude-mail, so opt in deliberately.

## Running with Docker

```bash
docker compose up -d          # build + run on http://localhost:8000
docker compose logs -f news   # follow logs
docker compose down           # stop (SQLite history persists in the named volume)
```

The image installs only the core requirements (~237MB) and `compose.yml` ships with
container hardening (non-root, read-only rootfs, dropped capabilities, resource limits).
Email is self-contained in the container via `SMTP_*` env vars (see `.env.example`);
set `MAIL_TO` and it sends digests/alerts directly — no dependency on other projects.

**Deploying to a server (Proxmox LXC + Docker):** see **[DEPLOY.md](DEPLOY.md)**, which
includes step-by-step setup and a **security guidance** section. Key point: the API has
**no built-in auth** (and `POST /api/refresh` is open) — don't expose it to the public
internet; keep it on your LAN/VPN or behind a reverse proxy with TLS + auth.

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

Requires `pip install -r requirements-llm.txt` and `GOOGLE_API_KEY` (read from the
environment or the claudecore `.env`). If either is missing it's a silent no-op.
Model defaults to `gemini-3.6-flash`.

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
