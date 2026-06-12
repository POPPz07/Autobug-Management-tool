"""
AutoRepro Enterprise — API Key Routes (V2.0)
api/api_key_routes.py

Enables programmatic access via X-API-Key header for CI/CD pipelines
and third-party integrations.

Endpoints:
  POST   /api/v1/api-keys          Create a new API key (ORG_ADMIN+)
  GET    /api/v1/api-keys          List all keys for this company
  DELETE /api/v1/api-keys/{id}     Revoke a key

Security model:
  - The raw key is shown ONCE at creation and never stored.
  - Only the SHA-256 hash is persisted (key_hash column).
  - Keys are prefixed with "ak_" for easy identification in logs.
  - Keys expire based on optional expires_at field.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.dependencies import Ctx
from api.responses import ok, ok_list
from db.models import UserRole
from db.models_v2 import ApiKey
from db.session import get_session

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


# ── Pydantic Schemas ──────────────────────────────────────────────


class ApiKeyCreate(BaseModel):
    """Request body for creating a new API key."""
    name: str
    expires_at: Optional[datetime] = None


class ApiKeyPublic(BaseModel):
    """Safe API key representation — never includes key_hash."""
    id: uuid.UUID
    name: str
    prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────────────


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash the raw API key for storage. Never store the raw key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _generate_raw_key() -> tuple[str, str]:
    """
    Generate a new API key.

    Returns:
        (raw_key, prefix)
        raw_key: The full key shown ONCE to the user (e.g. ak_3f7b9d2e1a...)
        prefix:  First 12 chars after ak_ (shown in UI for identification)
    """
    token = secrets.token_hex(32)          # 64 hex chars = 256 bits of entropy
    raw_key = f"ak_{token}"
    prefix  = raw_key[:12]                 # e.g. "ak_3f7b9d2e"
    return raw_key, prefix


# ── Routes ────────────────────────────────────────────────────────


@router.post("/", status_code=201)
def create_api_key(
    data: ApiKeyCreate,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """
    Create a new API key for the caller's company (ORG_ADMIN+ only).

    The raw key is returned ONCE in the response and never stored.
    Save it immediately — it cannot be recovered.
    """
    # Only ORG_ADMIN and above can create API keys
    if ctx.role not in (UserRole.ORG_ADMIN, UserRole.PLATFORM_ADMIN):
        raise HTTPException(403, detail="ORG_ADMIN or above required to create API keys")

    raw_key, prefix = _generate_raw_key()
    key_hash        = _hash_key(raw_key)

    api_key = ApiKey(
        company_id         = ctx.company_id,
        created_by_user_id = ctx.user_id,
        name               = data.name,
        key_hash           = key_hash,
        prefix             = prefix,
        is_active          = True,
        expires_at         = data.expires_at,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    # Return raw key ONCE — caller must store it securely
    return ok({
        "id":       str(api_key.id),
        "name":     api_key.name,
        "prefix":   api_key.prefix,
        "raw_key":  raw_key,    # ← shown only once; not stored
        "is_active": api_key.is_active,
        "created_at": api_key.created_at.isoformat(),
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
        "warning": "Store this key now. It will NOT be shown again.",
    })


@router.get("/")
def list_api_keys(
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """List all active API keys for the caller's company."""
    stmt = select(ApiKey).where(
        ApiKey.company_id == ctx.company_id,
        ApiKey.is_active == True,  # noqa: E712
    ).order_by(ApiKey.created_at.desc())
    keys = db.exec(stmt).all()

    # Return safe public view — never expose key_hash
    safe_keys = [ApiKeyPublic.from_orm(k) for k in keys]
    return ok_list(safe_keys, limit=len(safe_keys), offset=0, total=len(safe_keys))


@router.delete("/{key_id}")
def revoke_api_key(
    key_id: uuid.UUID,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """
    Revoke (soft-deactivate) an API key.
    ORG_ADMIN can only revoke keys belonging to their company.
    """
    api_key = db.get(ApiKey, key_id)

    if not api_key:
        raise HTTPException(404, detail="API key not found")

    # Tenant isolation: only the owning company can revoke
    if api_key.company_id != ctx.company_id and not ctx.is_platform_admin:
        raise HTTPException(403, detail="Cannot revoke keys belonging to another company")

    api_key.is_active = False
    db.commit()

    return ok({"id": str(key_id), "revoked": True})
