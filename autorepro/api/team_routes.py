"""
AutoRepro Enterprise — Team Routes  /api/v1/teams/

All routes:
  - Enforce company_id isolation
  - Enforce is_deleted == False
  - Return standardized envelopes

Endpoints:
  GET    /api/v1/teams/          List teams (SUPERVISOR+)
  POST   /api/v1/teams/          Create team (MANAGER+)
  GET    /api/v1/teams/{id}      Get single team
  PATCH  /api/v1/teams/{id}      Update team (MANAGER+)
  DELETE /api/v1/teams/{id}      Soft-delete team (ORG_ADMIN+)
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import select, func

from api.auth import Perm, assert_same_company, require_min_role
from api.dependencies import Ctx, Page
from api.responses import conflict, not_found, ok, ok_list
from db.models import ActivityLog, Team, TeamCreate, TeamPublic, TeamUpdate, UserRole
from db.session import SessionDep
from utils.logger import get_logger

log = get_logger(__name__)

teams_router = APIRouter(prefix="/api/v1/teams", tags=["teams"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_team_or_404(session, team_id: uuid.UUID) -> Team:
    team = session.get(Team, team_id)
    if not team or team.is_deleted:
        raise not_found("team", team_id)
    return team


# ═══════════════════════════════════════════════════════════════════
# LIST TEAMS
# ═══════════════════════════════════════════════════════════════════

@teams_router.get("")
def list_teams(
    session: SessionDep,
    ctx:     Ctx,
    page:    Page,
    _:       None = Depends(require_min_role(UserRole.SUPERVISOR)),
):
    stmt = (
        select(Team)
        .where(Team.is_deleted == False)  # noqa: E712
    )
    if ctx.company_id:
        stmt = stmt.where(Team.company_id == ctx.company_id)

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    teams = session.exec(
        stmt.order_by(Team.name)
            .offset(page.offset)
            .limit(page.limit)
    ).all()

    return ok_list(
        [TeamPublic.model_validate(t) for t in teams],
        limit=page.limit, offset=page.offset, total=total,
    )


# ═══════════════════════════════════════════════════════════════════
# CREATE TEAM
# ═══════════════════════════════════════════════════════════════════

@teams_router.post("", status_code=201)
def create_team(
    body:    TeamCreate,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_min_role(UserRole.MANAGER)),
):
    # Prevent duplicate names within the same company
    existing = session.exec(
        select(Team)
        .where(Team.name == body.name)
        .where(Team.is_deleted == False)  # noqa: E712
        .where(Team.company_id == ctx.user.company_id)
    ).first()
    if existing:
        raise conflict(f"A team named '{body.name}' already exists in this company")

    team = Team(
        name          = body.name,
        description   = body.description,
        supervisor_id = body.supervisor_id,
        company_id    = ctx.user.company_id,
    )
    session.add(team)
    session.flush()

    session.add(ActivityLog(
        entity_type   = "team",
        entity_id     = team.id,
        action        = "TEAM_CREATED",
        user_id       = ctx.user_id,
        company_id    = ctx.user.company_id,
        metadata_json = {"name": team.name},
    ))
    session.commit()
    session.refresh(team)

    log.info("team_created", team_id=str(team.id), by=str(ctx.user_id))
    return ok(TeamPublic.model_validate(team))


# ═══════════════════════════════════════════════════════════════════
# GET SINGLE TEAM
# ═══════════════════════════════════════════════════════════════════

@teams_router.get("/{team_id}")
def get_team(
    team_id: uuid.UUID,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_min_role(UserRole.SUPERVISOR)),
):
    team = _get_team_or_404(session, team_id)
    assert_same_company(ctx.user, team.company_id)
    return ok(TeamPublic.model_validate(team))


# ═══════════════════════════════════════════════════════════════════
# UPDATE TEAM
# ═══════════════════════════════════════════════════════════════════

@teams_router.patch("/{team_id}")
def update_team(
    team_id: uuid.UUID,
    body:    TeamUpdate,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_min_role(UserRole.MANAGER)),
):
    team = _get_team_or_404(session, team_id)
    assert_same_company(ctx.user, team.company_id)

    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(team, key, value)
    team.updated_at = _utcnow()
    session.add(team)
    session.commit()
    session.refresh(team)
    return ok(TeamPublic.model_validate(team))


# ═══════════════════════════════════════════════════════════════════
# SOFT DELETE TEAM
# ═══════════════════════════════════════════════════════════════════

@teams_router.delete("/{team_id}", status_code=204)
def delete_team(
    team_id: uuid.UUID,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_min_role(UserRole.ORG_ADMIN)),
):
    team = _get_team_or_404(session, team_id)
    assert_same_company(ctx.user, team.company_id)

    team.is_deleted = True
    team.updated_at = _utcnow()
    session.add(team)

    session.add(ActivityLog(
        entity_type   = "team",
        entity_id     = team.id,
        action        = "TEAM_DELETED",
        user_id       = ctx.user_id,
        company_id    = team.company_id,
    ))
    session.commit()
    log.info("team_soft_deleted", team_id=str(team_id), by=str(ctx.user_id))
