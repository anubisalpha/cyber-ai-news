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

import base64
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from email.utils import format_datetime
from xml.sax.saxutils import escape as xml_escape

from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from . import config
from .service import NewsService

_AUTH_USER = os.environ.get("AUTH_USER", "").strip()
_AUTH_PASS = os.environ.get("AUTH_PASS", "").strip()


class _BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Health endpoint is always open so container probes work without credentials.
        if request.url.path == "/api/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                if secrets.compare_digest(user, _AUTH_USER) and secrets.compare_digest(pw, _AUTH_PASS):
                    return await call_next(request)
            except Exception:  # noqa: BLE001
                pass
        return StarletteResponse(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="cyber-ai-news"'},
        )

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

@asynccontextmanager
async def lifespan(app: FastAPI):
    from . import scheduler  # noqa: PLC0415
    if scheduler.start_background():
        print("[api] background scheduler started (schedule.enabled=true)")
    yield
    scheduler.stop_background()


app = FastAPI(
    title="cyber-ai-news",
    description="Config-driven cybersecurity + AI news aggregator.",
    version="0.1.0",
    lifespan=lifespan,
)

# Optional built-in basic auth — active when AUTH_USER + AUTH_PASS are both set.
# Use this when running the container directly (without a proxy handling auth).
# /api/health is always open so healthcheck probes work without credentials.
if _AUTH_USER and _AUTH_PASS:
    app.add_middleware(_BasicAuthMiddleware)

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
    offset: int = Query(0, ge=0),
    include_duplicates: bool = True,
):
    items, total = _svc().query_page(
        domain=domain, category=category, severity=severity, region=region,
        source=source, q=q, sort=sort, limit=limit, offset=offset,
        include_duplicates=include_duplicates,
    )
    return {
        "count": len(items),
        "total": total,
        "offset": offset,
        "limit": limit,
        "articles": [a.to_dict() for a in items],
    }


@app.get("/api/categories")
def get_categories():
    return config.load_categories()


@app.get("/api/sources")
def get_sources():
    return {"sources": config.load_sources(enabled_only=False)}


@app.get("/api/sources/health")
def get_source_health():
    return _svc().source_health()


@app.get("/api/watchlists")
def get_watchlists():
    return {"watchlists": _svc().watchlists()}


@app.get("/api/watchlists/{name}")
def get_watchlist(
    name: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: Optional[str] = Query(None, pattern="^(date|weight)$"),
):
    result = _svc().watchlist_page(name, limit=limit, offset=offset, sort=sort)
    if result is None:
        return Response(status_code=404, content=f"No watchlist '{name}'")
    return result


@app.get("/api/stats")
def get_stats():
    return _svc().stats()


@app.get("/api/overview")
def get_overview(highlights: int = Query(8, ge=1, le=20)):
    return _svc().overview(highlights=highlights)


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


@app.get("/feed.xml")
def rss_feed(
    request: Request,
    watchlist: Optional[str] = None,
    domain: Optional[str] = Query(None, pattern="^(cyber|ai)$"),
    category: Optional[str] = None,
    severity: Optional[str] = Query(None, pattern="^(critical|high|medium|low)$"),
    region: Optional[str] = Query(None, pattern="^(us|eu|uk|apac)$"),
    q: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    svc = _svc()
    title = "Cyber + AI News"
    if watchlist:
        wl = config.find_watchlist(watchlist)
        if wl is None:
            return Response(status_code=404, content=f"No watchlist '{watchlist}'")
        f = wl.get("filters", {})
        domain, category, severity = f.get("domain"), f.get("category"), f.get("severity")
        region, q = f.get("region"), f.get("q")
        title += " — " + wl.get("label", watchlist)
    items, _ = svc.query_page(
        domain=domain, category=category, severity=severity, region=region,
        q=q, sort="date", limit=limit, include_duplicates=False,
    )
    base = str(request.base_url).rstrip("/")
    if not watchlist:
        bits = [b for b in (domain, category, severity, region, q) if b]
        if bits:
            title += " — " + " · ".join(bits)

    def item_xml(a):
        cats = "".join(f"<category>{xml_escape(c)}</category>" for c in a.categories)
        pub = ""
        if a.published:
            try:
                pub = f"<pubDate>{format_datetime(datetime.fromisoformat(a.published))}</pubDate>"
            except ValueError:
                pass
        desc = xml_escape((a.summary or "")[:500])
        return (
            f"<item><title>{xml_escape(a.title)}</title>"
            f"<link>{xml_escape(a.url)}</link>"
            f"<guid isPermaLink='false'>{a.id}</guid>"
            f"<source>{xml_escape(a.source)}</source>{cats}{pub}"
            f"<description>{desc}</description></item>"
        )

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>{xml_escape(title)}</title>"
        f"<link>{base}/</link>"
        "<description>Filtered cybersecurity + AI news</description>"
        f"<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>"
        + "".join(item_xml(a) for a in items)
        + "</channel></rss>"
    )
    return Response(content=body, media_type="application/rss+xml")


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
