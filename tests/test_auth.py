"""Tests for the optional built-in basic auth middleware."""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from src.api import _BasicAuthMiddleware


def _make_app(user: str, password: str) -> TestClient:
    """Return a TestClient for a minimal app with BasicAuthMiddleware active."""
    app = FastAPI()
    app.add_middleware(_BasicAuthMiddleware, user=user, password=password)

    @app.get("/api/health")
    def health():
        return JSONResponse({"status": "ok"})

    @app.get("/protected")
    def protected():
        return JSONResponse({"data": "secret"})

    return TestClient(app, raise_server_exceptions=False)


def _basic(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


# --- middleware active ---

client_auth = _make_app("admin", "testpass")


def test_no_credentials_returns_401():
    r = client_auth.get("/protected")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


def test_wrong_password_returns_401():
    r = client_auth.get("/protected", headers={"Authorization": _basic("admin", "wrong")})
    assert r.status_code == 401


def test_wrong_username_returns_401():
    r = client_auth.get("/protected", headers={"Authorization": _basic("hacker", "testpass")})
    assert r.status_code == 401


def test_correct_credentials_pass():
    r = client_auth.get("/protected", headers={"Authorization": _basic("admin", "testpass")})
    assert r.status_code == 200
    assert r.json()["data"] == "secret"


def test_health_always_open():
    """Health endpoint must be reachable without credentials (for container probes)."""
    r = client_auth.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_malformed_auth_header_returns_401():
    r = client_auth.get("/protected", headers={"Authorization": "Basic !!!notbase64!!!"})
    assert r.status_code == 401


def test_non_basic_scheme_returns_401():
    r = client_auth.get("/protected", headers={"Authorization": "Bearer sometoken"})
    assert r.status_code == 401


# --- middleware inactive (no env vars set) ---

def test_no_middleware_when_vars_unset():
    """When AUTH_USER/AUTH_PASS are blank, the app should serve without any auth."""
    app = FastAPI()

    @app.get("/open")
    def open_route():
        return JSONResponse({"ok": True})

    client = TestClient(app)
    r = client.get("/open")
    assert r.status_code == 200
