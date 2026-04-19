"""
AutoRepro Enterprise — Comment Routes  /api/v1/bugs/{bug_id}/comments/

Endpoints:
  GET    /api/v1/bugs/{bug_id}/comments/            List comments (paginated)
  POST   /api/v1/bugs/{bug_id}/comments/            Add comment
  DELETE /api/v1/bugs/{bug_id}/comments/{comment_id} Delete own comment (or MANAGER+)
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import select, func

from api.auth import assert_same_company, require_permission, Perm
from api.dependencies import Ctx, Page
from api.responses import bad_request, forbidden, not_found, ok, ok_list
from db.models import ActivityLog, Bug, Comment, CommentCreate, CommentPublic, UserRole
from db.session import SessionDep
from utils.logger import get_logger

log = get_logger(__name__)

comments_router = APIRouter(prefix="/api/v1/bugs", tags=["comments"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_bug_or_404(session, bug_id: uuid.UUID) -> Bug:
    bug = session.get(Bug, bug_id)
    if not bug or bug.is_deleted:
        raise not_found("bug", bug_id)
    return bug


# ═══════════════════════════════════════════════════════════════════
# LIST COMMENTS
# ═══════════════════════════════════════════════════════════════════

@comments_router.get("/{bug_id}/comments")
def list_comments(
    bug_id:  uuid.UUID,
    session: SessionDep,
    ctx:     Ctx,
    page:    Page,
    _:       None = Depends(require_permission(Perm.BUG_READ)),
):
    bug = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    stmt = (
        select(Comment)
        .where(Comment.bug_id == bug_id)
        .where(Comment.is_deleted == False)  # noqa: E712
        .order_by(Comment.created_at)
    )
    total    = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    comments = session.exec(stmt.offset(page.offset).limit(page.limit)).all()

    return ok_list(
        [CommentPublic.model_validate(c) for c in comments],
        limit=page.limit, offset=page.offset, total=total,
    )


# ═══════════════════════════════════════════════════════════════════
# ADD COMMENT
# ═══════════════════════════════════════════════════════════════════

@comments_router.post("/{bug_id}/comments", status_code=201)
def add_comment(bug_id: uuid.UUID, body: CommentCreate, session: SessionDep, ctx: Ctx):
    bug = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    if not body.message.strip():
        raise bad_request("Comment message cannot be empty")

    comment = Comment(
        bug_id     = bug_id,
        user_id    = ctx.user_id,
        message    = body.message.strip(),
        parent_id  = body.parent_id,
        company_id = bug.company_id,
    )
    session.add(comment)
    session.flush()   # get comment.id before commit

    session.add(ActivityLog(
        entity_type   = "comment",
        entity_id     = comment.id,
        action        = "COMMENT_ADDED",
        user_id       = ctx.user_id,
        company_id    = bug.company_id,
        metadata_json = {"bug_id": str(bug_id)},
    ))
    session.commit()
    session.refresh(comment)

    log.info("comment_added", bug_id=str(bug_id), by=str(ctx.user_id))
    return ok(CommentPublic.model_validate(comment))


# ═══════════════════════════════════════════════════════════════════
# DELETE COMMENT (soft)
# ═══════════════════════════════════════════════════════════════════

@comments_router.delete("/{bug_id}/comments/{comment_id}", status_code=204)
def delete_comment(
    bug_id:     uuid.UUID,
    comment_id: uuid.UUID,
    session:    SessionDep,
    ctx:        Ctx,
):
    """Soft-delete a comment. Authors can delete their own; MANAGER+ can delete any."""
    from api.auth import ROLE_LEVEL
    comment = session.get(Comment, comment_id)
    if not comment or comment.bug_id != bug_id or comment.is_deleted:
        raise not_found("comment", comment_id)

    is_author  = comment.user_id == ctx.user_id
    is_manager = ROLE_LEVEL.get(ctx.role, 0) >= ROLE_LEVEL[UserRole.MANAGER]

    if not is_author and not is_manager:
        raise forbidden("You can only delete your own comments")

    comment.is_deleted = True
    comment.updated_at = _utcnow()
    session.add(comment)
    session.commit()
