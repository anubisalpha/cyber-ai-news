"""User-level feed submission — any logged-in user can suggest an RSS feed.

Validate-only:   POST /user/feeds/validate
Validate + add:  POST /user/feeds
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import config
from .fetch import validate_feed_url

router = APIRouter(prefix="/user")


def _db(request: Request):
    return request.app.state.auth_db


def _require_auth(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _url_to_name(url: str, db) -> str:
    """Derive a unique slug from the feed URL's hostname."""
    host = urlparse(url).netloc.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", host).strip("-")[:32]
    name, i = slug, 2
    while db.get_source(name):
        name = f"{slug}-{i}"
        i += 1
    return name


class FeedRequest(BaseModel):
    url: str = Field(min_length=1)
    label: str = ""
    domain: str = Field(default="cyber", pattern=r"^(cyber|ai|both|releases)$")


@router.post("/feeds/validate")
def validate_feed(body: FeedRequest, request: Request):
    """Validate a feed URL without saving it."""
    _require_auth(request)
    settings = config.load_settings()
    return validate_feed_url(body.url, settings)


@router.post("/feeds", status_code=201)
def submit_feed(body: FeedRequest, request: Request):
    """Validate then add a new RSS feed to the global source pool."""
    user = _require_auth(request)
    db = _db(request)
    settings = config.load_settings()

    result = validate_feed_url(body.url, settings)
    if not result["ok"]:
        raise HTTPException(status_code=422, detail=result.get("error", "Invalid feed"))

    # Reject duplicate URLs
    existing = db.list_sources(enabled_only=False)
    if any(s["url"] == body.url for s in existing):
        raise HTTPException(status_code=409, detail="This feed URL is already registered")

    name = _url_to_name(body.url, db)
    label = (body.label or result.get("title") or name).strip()[:100]

    db.create_source(
        {
            "name": name,
            "label": label,
            "url": body.url,
            "type": "rss",
            "domain": body.domain,
            "weight": 50,
            "tags": "",
            "enabled": True,
        },
        created_by=user["username"],
    )
    return {"ok": True, "name": name, "label": label, "item_count": result.get("item_count", 0)}
