"""FastAPI REST API + web dashboard for the news service.

Run:
    uvicorn src.api:app --reload --port 8000
Then open http://127.0.0.1:8000/

Endpoints:
    GET  /                 -> web dashboard (static SPA)
    GET  /api/articles     -> filtered article list
    GET  /api/categories   -> the taxonomy
    GET  /api/sources      -> configured sources
    GET  /api/stats        -> aggregate counts
    POST /api/refresh      -> trigger a fetch+classify in the background
    GET  /api/health       -> liveness + last-refresh info
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .service import NewsService

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

app = FastAPI(
    title="cyber-ai-news",
    description="Config-driven cybersecurity + AI news aggregator.",
    version="0.1.0",
)

# In-memory refresh status (single-process; fine for the dashboard).
_refresh_state: dict = {"running": False, "last_finished": None, "last_error": None}


def _svc() -> NewsService:
    return NewsService()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/articles")
def get_articles(
    domain: Optional[str] = Query(None, pattern="^(cyber|ai)$"),
    category: Optional[str] = None,
    severity: Optional[str] = Query(None, pattern="^(critical|high|medium|low)$"),
    region: Optional[str] = Query(None, pattern="^(us|eu|uk|apac)$"),
    source: Optional[str] = None,
    q: Optional[str] = None,
    sort: Optional[str] = Query(None, pattern="^(date|weight)$"),
    limit: int = Query(50, ge=1, le=500),
):
    items = _svc().query(
        domain=domain, category=category, severity=severity, region=region,
        source=source, q=q, sort=sort, limit=limit,
    )
    return {"count": len(items), "articles": [a.to_dict() for a in items]}


@app.get("/api/categories")
def get_categories():
    return config.load_categories()


@app.get("/api/sources")
def get_sources():
    return {"sources": config.load_sources(enabled_only=False)}


@app.get("/api/stats")
def get_stats():
    return _svc().stats()


def _do_refresh():
    _refresh_state["running"] = True
    _refresh_state["last_error"] = None
    try:
        _svc().refresh(verbose=False)
        _refresh_state["last_finished"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:  # noqa: BLE001
        _refresh_state["last_error"] = str(exc)
    finally:
        _refresh_state["running"] = False


@app.post("/api/refresh")
def post_refresh(background: BackgroundTasks):
    if _refresh_state["running"]:
        return {"status": "already_running"}
    background.add_task(_do_refresh)
    return {"status": "started"}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "refresh": _refresh_state,
        "stored": _svc().stats()["total"],
    }


# ---------------------------------------------------------------------------
# Web dashboard (served last so /api/* wins)
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
