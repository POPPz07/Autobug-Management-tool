"""
AutoRepro Enterprise — Phase 1.5: Authentication & RBAC

This module is the single source of truth for:
  1. JWT token creation / verification  (user_id, company_id, role in payload)
  2. FastAPI dependencies               (CurrentUser, RequireRole)
  3. Role permission mapping            (ROLE_PERMISSIONS)
  4. require_role() decorator factory   (for route-level enforcement)
  5. Data access scoping rules          (company/team isolation helpers)

JWT payload structure:
  {
    "sub":        "<user_id (UUID str)>",
    "company_id": "<company_id (UUID str)>",
    "role":       "<UserRole value>",
    "exp":        <unix timestamp>
  }

RBAC hierarchy (lowest → highest):
  DEVELOPER < TESTER < SUPERVISOR < MANAGER < ORG_ADMIN < PLATFORM_ADMIN
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Annotated, Callable

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlmodel import Session, select

from db.models import Company, User, UserCreate, UserPublic, UserRole
from db.session import SessionDep
from utils.config import ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY
from utils.logger import get_logger

log = get_logger(__name__)

# ── Password hashing (Argon2id) ───────────────────────────────────
pwd_context = PasswordHash((Argon2Hasher(),))
DUMMY_HASH: str = pwd_context.hash("dummy-timing-equalizer-password")

# ── JWT config ────────────────────────────────────────────────────
ALGORITHM    = "HS256"
oauth2_scheme          = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ═══════════════════════════════════════════════════════════════════
# ROLE PERMISSION MAPPING
# ═══════════════════════════════════════════════════════════════════

# Numeric privilege level — higher = more power.
ROLE_LEVEL: dict[UserRole, int] = {
    UserRole.DEVELOPER:      1,
    UserRole.TESTER:         2,
    UserRole.SUPERVISOR:     3,
    UserRole.MANAGER:        4,
    UserRole.ORG_ADMIN:      5,
    UserRole.PLATFORM_ADMIN: 6,
    UserRole.SYSTEM:         7,   # internal only — highest privilege; no human can hold this
}

# Permission strings — use these as constants throughout the codebase.
class Perm:
    # Bug permissions
    BUG_CREATE         = "bug:create"
    BUG_READ           = "bug:read"
    BUG_UPDATE         = "bug:update"
    BUG_DELETE         = "bug:delete"          # soft delete only
    BUG_ASSIGN         = "bug:assign"
    BUG_TRIAGE         = "bug:triage"
    BUG_CLOSE          = "bug:close"

    # Execution permissions
    JOB_TRIGGER        = "job:trigger"          # run AutoRepro
    JOB_READ           = "job:read"
    JOB_FORCE_RETRY    = "job:force_retry"      # re-run after max_attempts

    # User management
    USER_READ          = "user:read"
    USER_MANAGE        = "user:manage"          # create / deactivate / change role
    USER_INVITE        = "user:invite"

    # Team management
    TEAM_CREATE        = "team:create"
    TEAM_MANAGE        = "team:manage"

    # Organisation management
    ORG_MANAGE         = "org:manage"           # edit company settings

    # Platform-level (PLATFORM_ADMIN only)
    PLATFORM_MANAGE    = "platform:manage"


# Role → granted permissions (additive; higher roles inherit lower ones implicitly
# via require_min_role, but explicit grants control fine-grained endpoint access).
ROLE_PERMISSIONS: dict[UserRole, set[str]] = {
    UserRole.DEVELOPER: {
        Perm.BUG_READ,
        Perm.BUG_CREATE,
        Perm.JOB_READ,
    },
    UserRole.TESTER: {
        Perm.BUG_READ,
        Perm.BUG_CREATE,
        Perm.BUG_UPDATE,
        Perm.JOB_TRIGGER,
        Perm.JOB_READ,
    },
    UserRole.SUPERVISOR: {
        Perm.BUG_READ,
        Perm.BUG_CREATE,
        Perm.BUG_UPDATE,
        Perm.BUG_ASSIGN,
        Perm.BUG_TRIAGE,
        Perm.JOB_TRIGGER,
        Perm.JOB_READ,
        Perm.TEAM_MANAGE,
        Perm.USER_READ,
    },
    UserRole.MANAGER: {
        Perm.BUG_READ,
        Perm.BUG_CREATE,
        Perm.BUG_UPDATE,
        Perm.BUG_DELETE,
        Perm.BUG_ASSIGN,
        Perm.BUG_TRIAGE,
        Perm.BUG_CLOSE,
        Perm.JOB_TRIGGER,
        Perm.JOB_READ,
        Perm.JOB_FORCE_RETRY,
        Perm.TEAM_CREATE,
        Perm.TEAM_MANAGE,
        Perm.USER_READ,
        Perm.USER_INVITE,
    },
    UserRole.ORG_ADMIN: {
        Perm.BUG_READ,
        Perm.BUG_CREATE,
        Perm.BUG_UPDATE,
        Perm.BUG_DELETE,
        Perm.BUG_ASSIGN,
        Perm.BUG_TRIAGE,
        Perm.BUG_CLOSE,
        Perm.JOB_TRIGGER,
        Perm.JOB_READ,
        Perm.JOB_FORCE_RETRY,
        Perm.TEAM_CREATE,
        Perm.TEAM_MANAGE,
        Perm.USER_READ,
        Perm.USER_MANAGE,
        Perm.USER_INVITE,
        Perm.ORG_MANAGE,
    },
    UserRole.PLATFORM_ADMIN: {
        # All permissions
        Perm.BUG_READ, Perm.BUG_CREATE, Perm.BUG_UPDATE, Perm.BUG_DELETE,
        Perm.BUG_ASSIGN, Perm.BUG_TRIAGE, Perm.BUG_CLOSE,
        Perm.JOB_TRIGGER, Perm.JOB_READ, Perm.JOB_FORCE_RETRY,
        Perm.TEAM_CREATE, Perm.TEAM_MANAGE,
        Perm.USER_READ, Perm.USER_MANAGE, Perm.USER_INVITE,
        Perm.ORG_MANAGE,
        Perm.PLATFORM_MANAGE,
    },
}


def has_permission(user: User, perm: str) -> bool:
    """Return True if the user's role grants the given permission."""
    return perm in ROLE_PERMISSIONS.get(user.role, set())


def has_min_role(user: User, min_role: UserRole) -> bool:
    """Return True if user's role level >= min_role level."""
    return ROLE_LEVEL.get(user.role, 0) >= ROLE_LEVEL.get(min_role, 0)


# ═══════════════════════════════════════════════════════════════════
# PASSWORD HELPERS
# ═══════════════════════════════════════════════════════════════════

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ═══════════════════════════════════════════════════════════════════
# JWT HELPERS
# ═══════════════════════════════════════════════════════════════════

def create_access_token(user: User) -> str:
    """
    Create a signed JWT embedding user_id, company_id, and role.
    These three fields are sufficient for the API to make all auth decisions
    without hitting the database on every request.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub":        str(user.id),
        "company_id": str(user.company_id),
        "role":       user.role.value,
        "exp":        expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and verify a JWT. Raises HTTPException on any failure.
    Returns the raw payload dict.
    """
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ═══════════════════════════════════════════════════════════════════
# FASTAPI DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════

def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: SessionDep,
) -> User:
    """
    FastAPI dependency — decode JWT, load User from DB.

    Returns the full User object (with company_id, role, is_active).
    Raises 401 if token invalid / expired.
    Raises 403 if account is inactive.
    """
    payload  = decode_token(token)
    user_id  = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    user = session.get(User, uuid.UUID(user_id))
    if user is None or user.is_deleted:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive account")
    return user


def get_optional_current_user(
    token: Annotated[str | None, Depends(optional_oauth2_scheme)],
    session: SessionDep,
) -> User | None:
    """Optional variant — returns None when no token is present (public endpoints)."""
    if not token:
        return None
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            return None
        user = session.get(User, uuid.UUID(user_id))
        return user if (user and not user.is_deleted and user.is_active) else None
    except HTTPException:
        return None


# Reusable type aliases for route signatures
CurrentUser  = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_current_user)]


# ═══════════════════════════════════════════════════════════════════
# require_role DECORATOR FACTORY
# ═══════════════════════════════════════════════════════════════════

def require_role(*roles: UserRole):
    """
    FastAPI dependency factory — raises 403 if current user's role
    is not in the provided set.

    Usage:
        @router.get("/sensitive")
        def endpoint(
            current_user: CurrentUser,
            _: None = Depends(require_role(UserRole.MANAGER, UserRole.ORG_ADMIN)),
        ):
            ...
    """
    def dependency(current_user: CurrentUser):
        if current_user.role not in roles:
            allowed = ", ".join(r.value for r in roles)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {allowed}",
            )
    return Depends(dependency)


def require_min_role(min_role: UserRole):
    """
    FastAPI dependency factory — raises 403 if current user's role level
    is strictly below min_role.

    Usage:
        @router.delete("/{bug_id}")
        def delete_bug(
            current_user: CurrentUser,
            _: None = Depends(require_min_role(UserRole.MANAGER)),
        ):
            ...
    """
    def dependency(current_user: CurrentUser):
        if not has_min_role(current_user, min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires at least {min_role.value} role",
            )
    return Depends(dependency)


def require_permission(perm: str):
    """
    FastAPI dependency factory — raises 403 if the current user's role
    does not grant the specific permission string.

    Usage:
        @router.post("/{bug_id}/run")
        def trigger_job(
            current_user: CurrentUser,
            _: None = Depends(require_permission(Perm.JOB_TRIGGER)),
        ):
            ...
    """
    def dependency(current_user: CurrentUser):
        if not has_permission(current_user, perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {perm}",
            )
    return Depends(dependency)


# ═══════════════════════════════════════════════════════════════════
# DATA ACCESS SCOPING RULES
# ═══════════════════════════════════════════════════════════════════

def assert_same_company(current_user: User, entity_company_id: uuid.UUID) -> None:
    """
    Raise 403 if the entity belongs to a different company.
    PLATFORM_ADMIN bypasses this check (cross-tenant access allowed).
    """
    if current_user.role == UserRole.PLATFORM_ADMIN:
        return
    if current_user.company_id != entity_company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant access denied",
        )


def assert_team_access(current_user: User, entity_team_id: uuid.UUID | None) -> None:
    """
    Enforce team-level scoping for DEVELOPER and TESTER roles.
    These roles can ONLY access bugs/jobs belonging to their own team.
    SUPERVISOR and above may access all teams within their company.
    """
    if has_min_role(current_user, UserRole.SUPERVISOR):
        return   # Supervisor+ sees all teams in their company
    if entity_team_id is None:
        return   # Unassigned bugs are visible to all roles
    if current_user.team_id != entity_team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: bug belongs to a different team",
        )


def build_company_filter(current_user: User) -> uuid.UUID | None:
    """
    Return the company_id to filter queries by, or None if PLATFORM_ADMIN
    (who can query across all companies).

    Usage in route:
        company_filter = build_company_filter(current_user)
        stmt = select(Bug).where(Bug.is_deleted == False)
        if company_filter:
            stmt = stmt.where(Bug.company_id == company_filter)
    """
    if current_user.role == UserRole.PLATFORM_ADMIN:
        return None
    return current_user.company_id


# ═══════════════════════════════════════════════════════════════════
# AUTH ROUTES  (/api/v1/auth/...)
# ═══════════════════════════════════════════════════════════════════

from pydantic import BaseModel as _BM


class TokenResponse(_BM):
    access_token: str
    token_type:   str = "bearer"


class RoleUpdateRequest(_BM):
    role: UserRole


@auth_router.post("/login", response_model=TokenResponse)
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
):
    """
    Authenticate and return JWT.

    Timing-attack prevention: always run Argon2 verification even on unknown
    emails so the response time is indistinguishable from a real failure.
    """
    user = session.exec(
        select(User)
        .where(User.email == form_data.username)
        .where(User.is_deleted == False)   # noqa: E712
    ).first()

    if user is None:
        verify_password("dummy", DUMMY_HASH)    # equalize timing
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive account")

    token = create_access_token(user)
    log.info("user_logged_in", user_id=str(user.id), role=user.role.value)
    return TokenResponse(access_token=token)


@auth_router.post("/register", response_model=UserPublic, status_code=201)
def register(user_in: UserCreate, session: SessionDep):
    """
    Register a new user.

    Public self-registration is intentionally restricted to lower roles
    (DEVELOPER, TESTER). Higher roles must be created by ORG_ADMIN or above.
    """
    SELF_REGISTER_ROLES = {UserRole.DEVELOPER, UserRole.TESTER}
    if user_in.role not in SELF_REGISTER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Self-registration only allowed for: "
                   f"{', '.join(r.value for r in SELF_REGISTER_ROLES)}",
        )

    # Verify the company exists
    company = session.get(Company, user_in.company_id)
    if not company or company.is_deleted:
        raise HTTPException(status_code=404, detail="Company not found")

    existing = session.exec(
        select(User).where(User.email == user_in.email)
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        full_name     = user_in.full_name,
        email         = user_in.email,
        password_hash = hash_password(user_in.password),
        role          = user_in.role,
        company_id    = user_in.company_id,
        team_id       = user_in.team_id,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    log.info("user_registered", user_id=str(user.id), role=user.role.value)
    return UserPublic.model_validate(user)


@auth_router.get("/me", response_model=UserPublic)
def get_me(current_user: CurrentUser):
    """Return the currently authenticated user's profile."""
    return UserPublic.model_validate(current_user)


@auth_router.get("/users", response_model=list[UserPublic])
def list_users(
    session: SessionDep,
    current_user: CurrentUser,
    _: None = Depends(require_min_role(UserRole.SUPERVISOR)),
):
    """
    List users scoped to the caller's company.
    SUPERVISOR+ can list all users in their company.
    PLATFORM_ADMIN sees all users across all companies.
    """
    stmt = select(User).where(User.is_deleted == False)  # noqa: E712
    company_filter = build_company_filter(current_user)
    if company_filter:
        stmt = stmt.where(User.company_id == company_filter)
    stmt = stmt.order_by(User.created_at.desc())
    return [UserPublic.model_validate(u) for u in session.exec(stmt).all()]


@auth_router.patch("/users/{user_id}/role", response_model=UserPublic)
def update_user_role(
    user_id: uuid.UUID,
    body: RoleUpdateRequest,
    session: SessionDep,
    current_user: CurrentUser,
    _: None = Depends(require_min_role(UserRole.ORG_ADMIN)),
):
    """Change a user's role — ORG_ADMIN or PLATFORM_ADMIN only."""
    user = session.get(User, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found")
    assert_same_company(current_user, user.company_id)

    # Prevent privilege escalation above own role
    if ROLE_LEVEL.get(body.role, 0) >= ROLE_LEVEL.get(current_user.role, 0):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot assign a role equal to or higher than your own",
        )

    user.role       = body.role
    user.updated_at = datetime.now(timezone.utc)
    session.add(user)
    session.commit()
    session.refresh(user)
    log.info("user_role_updated", target=str(user_id), new_role=body.role.value, by=str(current_user.id))
    return UserPublic.model_validate(user)


@auth_router.patch("/users/{user_id}/deactivate", response_model=UserPublic)
def deactivate_user(
    user_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    _: None = Depends(require_min_role(UserRole.ORG_ADMIN)),
):
    """Deactivate a user account — ORG_ADMIN or PLATFORM_ADMIN only."""
    if str(user_id) == str(current_user.id):
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
    user = session.get(User, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found")
    assert_same_company(current_user, user.company_id)

    user.is_active  = False
    user.updated_at = datetime.now(timezone.utc)
    session.add(user)
    session.commit()
    session.refresh(user)
    log.info("user_deactivated", target=str(user_id), by=str(current_user.id))
    return UserPublic.model_validate(user)
