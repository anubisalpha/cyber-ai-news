"""API smoke tests (skipped if the test client / httpx isn't installed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

TestClient = pytest.importorskip("fastapi.testclient").TestClient

from src.api import app  # noqa: E402

client = TestClient(app)


def test_articles_response_shape():
    r = client.get("/api/articles?limit=5")
    assert r.status_code == 200
    body = r.json()
    for key in ("count", "total", "offset", "limit", "articles"):
        assert key in body
    assert body["limit"] == 5
    assert isinstance(body["articles"], list)


def test_pagination_params_respected():
    r = client.get("/api/articles?limit=3&offset=0")
    assert r.status_code == 200
    assert r.json()["offset"] == 0
    assert len(r.json()["articles"]) <= 3


def test_invalid_domain_rejected():
    assert client.get("/api/articles?domain=banana").status_code == 422


def test_stats_and_health():
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"


def test_categories_endpoint():
    body = client.get("/api/categories").json()
    assert "categories" in body
