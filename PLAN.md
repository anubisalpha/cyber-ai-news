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
    ⏳ live-validated pending `google-generativeai` install (off by default).
- ✅ **Near-duplicate detection** (`src/dedupe.py`) — token-set Jaccard,
  cross-source-only + time-window guards to avoid templated-title false merges;
  sets `cluster_id` / `duplicate_of`.
- ✅ **Tests + pagination** — 28 tests (classify/dedupe/storage/llm/api);
  `/api/articles` now returns `total/offset/limit` + `include_duplicates`.

## Phase 2 — Delivery & automation  *(later)*

- ⬜ Email digest via existing `claude-mail` (daily/weekly, filterable).
- ⬜ Scheduled auto-refresh (Task Scheduler / claudecore-hub).
- ⬜ RSS/Atom output feed of a filtered view.
- ⬜ Per-source health monitoring + dashboard indicator.

## Phase 3 — Personalization & UX  *(later)*

- ⬜ Saved filters / named watchlists (config-driven "lists").
- ⬜ Keyword alerts (notify on match).
- ⬜ Dashboard polish: stats/charts panel, read/bookmark state, relative times, pagination UI.
- ⬜ Dockerize + deploy.

## Decisions locked in

- Classification: **keywords + LLM fallback** (LLM only on uncategorized, to control cost).
- Storage: **SQLite**, JSON kept as import/export format.
