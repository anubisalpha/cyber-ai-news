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

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from email.utils import format_datetime
from xml.sax.saxutils import escape as xml_escape

from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from . import config
from .service import NewsService
from .storage import Storage
from .auth import verify_password, hash_password, create_session_token, verify_session_token
from .ldap_auth import verify_ldap

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

_OPEN_API_PATHS = {"/api/health", "/auth/login", "/auth/logout", "/auth/me"}


class _SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        needs_auth = path.startswith("/api/") or path.startswith("/admin/")
        if not needs_auth or path in _OPEN_API_PATHS:
            return await call_next(request)

        token = request.cookies.get("session")
        user_id = verify_session_token(token) if token else None
        if user_id is None:
            return StarletteResponse(
                status_code=401,
                content='{"detail":"Not authenticated"}',
                headers={"Content-Type": "application/json"},
            )

        db: Storage = request.app.state.auth_db
        user = db.get_user_by_id(user_id)
        if user is None:
            return StarletteResponse(
                status_code=401,
                content='{"detail":"Session invalid"}',
                headers={"Content-Type": "application/json"},
            )
        request.state.user = user
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Shared Storage instance for user/auth operations
    settings = config.load_settings()
    db_file = settings.get("storage", {}).get("db_file", "data/news.db")
    app.state.auth_db = Storage(ROOT / db_file)

    # Bootstrap admin account from env vars if no users exist yet
    auth_user = os.environ.get("AUTH_USER", "").strip()
    auth_pass = os.environ.get("AUTH_PASS", "").strip()
    db: Storage = app.state.auth_db
    if auth_user and auth_pass and not db.has_any_users():
        db.create_user(auth_user, hash_password(auth_pass), is_admin=True,
                       force_password_change=True)
        print(f"[api] bootstrap admin account created: {auth_user!r} (change password on first login)")

    # Seed sources table from sources.yaml on first run
    if not db.has_any_sources():
        yaml_sources = config.load_sources(enabled_only=False)
        if yaml_sources:
            db.seed_sources(yaml_sources)
            print(f"[api] seeded {len(yaml_sources)} sources from sources.yaml")

    from . import scheduler  # noqa: PLC0415
    if scheduler.start_background():
        print("[api] background scheduler started (schedule.enabled=true)")
    yield
    scheduler.stop_background()
    app.state.auth_db.close()


app = FastAPI(
    title="cyber-ai-news",
    description="Config-driven cybersecurity + AI news aggregator.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(_SessionAuthMiddleware)

from .admin import router as admin_router  # noqa: E402
app.include_router(admin_router)

# In-memory refresh status (single-process; fine for the dashboard).
_refresh_state: dict = {"running": False, "last_finished": None, "last_error": None}


def _svc() -> NewsService:
    return NewsService()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class _LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
def auth_login(body: _LoginRequest, request: Request):
    db: Storage = request.app.state.auth_db
    user = db.get_user_by_username(body.username)

    # 1. Local auth — password_hash must be a real bcrypt hash (not the LDAP sentinel)
    if user and user.get("auth_source", "local") == "local" and \
            verify_password(body.password, user["password_hash"]):
        db.update_user_last_login(user["id"])
        token = create_session_token(user["id"])
        resp = Response(content='{"ok":true}', media_type="application/json")
        resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=8 * 3600)
        return resp

    # 2. LDAP auth — try if AD config is present
    ad_cfg = db.get_ad_config()
    if ad_cfg and ad_cfg.get("server"):
        user_dn, is_admin, email = verify_ldap(ad_cfg, body.username, body.password)
        if user_dn:
            user_id = db.upsert_ldap_user(body.username, is_admin, email=email)
            db.update_user_last_login(user_id)
            token = create_session_token(user_id)
            resp = Response(content='{"ok":true}', media_type="application/json")
            resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=8 * 3600)
            return resp

    return Response(status_code=401, content='{"detail":"Invalid credentials"}',
                    media_type="application/json")


@app.post("/auth/logout")
def auth_logout():
    resp = Response(content='{"ok":true}', media_type="application/json")
    resp.delete_cookie("session")
    return resp


@app.get("/auth/me")
def auth_me(request: Request):
    token = request.cookies.get("session")
    user_id = verify_session_token(token) if token else None
    if user_id is None:
        return Response(status_code=401, content='{"detail":"Not authenticated"}',
                        media_type="application/json")
    db: Storage = request.app.state.auth_db
    user = db.get_user_by_id(user_id)
    if user is None:
        return Response(status_code=401, content='{"detail":"Session invalid"}',
                        media_type="application/json")
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "force_password_change": bool(user["force_password_change"]),
    }


# ---------------------------------------------------------------------------
# User sources
# ---------------------------------------------------------------------------

class _UserSourcesRequest(BaseModel):
    sources: list[str]


@app.get("/user/sources")
def get_user_sources(request: Request):
    user = request.state.user
    db: Storage = request.app.state.auth_db
    all_sources = db.list_sources(enabled_only=True)
    user_srcs = set(db.get_user_sources(user["id"]))
    return {
        "sources": [
            {**src, "selected": src["name"] in user_srcs}
            for src in all_sources
        ],
        "has_selection": len(user_srcs) > 0,
    }


@app.put("/user/sources")
def set_user_sources(body: _UserSourcesRequest, request: Request):
    user = request.state.user
    db: Storage = request.app.state.auth_db
    db.set_user_sources(user["id"], body.sources)
    return {"ok": True}


def _user_source_list(request: Request) -> "list[str] | None":
    """Return user's selected source names, or None (meaning: all) if none set."""
    user = getattr(request.state, "user", None)
    if user is None:
        return None
    srcs = request.app.state.auth_db.get_user_sources(user["id"])
    return srcs if srcs else None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/articles")
def get_articles(
    request: Request,
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
    source_list = _user_source_list(request)
    items, total = _svc().query_page(
        domain=domain, category=category, severity=severity, region=region,
        source=source, q=q, sort=sort, limit=limit, offset=offset,
        include_duplicates=include_duplicates, source_list=source_list,
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
def get_overview(request: Request, highlights: int = Query(8, ge=1, le=20)):
    source_list = _user_source_list(request)
    return _svc().overview(highlights=highlights, source_list=source_list)


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
