"""Admin API router — password management, user list, AD config."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/admin")


def _db(request: Request):
    return request.app.state.auth_db


def _require_auth(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _require_admin(request: Request) -> dict:
    user = _require_auth(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin required")
    return user


# ---------- password change --------------------------------------------------

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


@router.put("/password")
def change_password(body: PasswordChangeRequest, request: Request):
    from .auth import verify_password, hash_password
    user = _require_auth(request)
    db = _db(request)
    full = db.get_user_by_id(user["id"])
    if not verify_password(body.current_password, full["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    db.update_user_password(user["id"], hash_password(body.new_password))
    return {"ok": True}


# ---------- user list (admin only) -------------------------------------------

@router.get("/users")
def list_users(request: Request):
    _require_admin(request)
    return {"users": _db(request).list_users()}


# ---------- AD config (admin only) -------------------------------------------

class AdConfigRequest(BaseModel):
    server: str = Field(min_length=1)
    port: int = Field(default=389, ge=1, le=65535)
    use_tls: bool = False
    base_dn: str = ""
    bind_dn: str = ""
    bind_password: str = ""
    user_filter: str = ""
    group_dn: str = ""


@router.get("/ad")
def get_ad_config(request: Request):
    _require_admin(request)
    cfg = _db(request).get_ad_config()
    if cfg:
        cfg["bind_password"] = "***" if cfg.get("bind_password") else ""
    return {"ad_config": cfg}


@router.put("/ad")
def update_ad_config(body: AdConfigRequest, request: Request):
    _require_admin(request)
    fields = body.model_dump()
    # Don't overwrite the stored password if the field was left blank
    if not fields.get("bind_password"):
        fields.pop("bind_password", None)
    _db(request).upsert_ad_config(**fields)
    return {"ok": True}


# ---------- source management (admin only) ------------------------------------

class SourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, pattern=r"^[a-z0-9_-]+$")
    label: str = ""
    url: str = Field(min_length=1)
    type: str = Field(default="rss", pattern=r"^(rss|json)$")
    domain: str = Field(default="cyber", pattern=r"^(cyber|ai|both)$")
    weight: int = Field(default=50, ge=0, le=100)
    tags: str = ""
    enabled: bool = True


class SourceUpdateRequest(BaseModel):
    label: str | None = None
    url: str | None = None
    type: str | None = Field(default=None, pattern=r"^(rss|json)$")
    domain: str | None = Field(default=None, pattern=r"^(cyber|ai|both)$")
    weight: int | None = Field(default=None, ge=0, le=100)
    tags: str | None = None
    enabled: bool | None = None


@router.get("/sources")
def list_sources(request: Request):
    _require_admin(request)
    return {"sources": _db(request).list_sources(enabled_only=False)}


@router.post("/sources", status_code=201)
def create_source(body: SourceCreateRequest, request: Request):
    user = _require_admin(request)
    db = _db(request)
    if db.get_source(body.name):
        raise HTTPException(status_code=409, detail="Source already exists")
    db.create_source(body.model_dump(), created_by=user["username"])
    return {"ok": True, "name": body.name}


@router.put("/sources/{name}")
def update_source(name: str, body: SourceUpdateRequest, request: Request):
    user = _require_admin(request)
    db = _db(request)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="No changes provided")
    if not db.update_source(name, changes, changed_by=user["username"]):
        raise HTTPException(status_code=404, detail="Source not found")
    return {"ok": True}


@router.delete("/sources/{name}", status_code=200)
def delete_source(name: str, request: Request):
    user = _require_admin(request)
    db = _db(request)
    if not db.delete_source(name, changed_by=user["username"]):
        raise HTTPException(status_code=404, detail="Source not found")
    return {"ok": True}


@router.get("/sources/changelog")
def source_changelog(request: Request, limit: int = 100):
    _require_admin(request)
    return {"changelog": _db(request).source_changelog(limit=limit)}
