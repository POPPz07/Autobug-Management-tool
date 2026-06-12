"""
AutoRepro Enterprise — Bulk Operations Service (V2.0)

Provides batch assign, status update, and soft delete for multiple bugs.
Each operation validates tenant isolation and skips invalid items gracefully.
"""

from uuid import UUID

from sqlmodel import Session

from db.models import Bug, BugStatus


def bulk_assign(
    db: Session, bug_ids: list[UUID], assigned_to_user_id: UUID, ctx
) -> int:
    """
    Assign multiple bugs to a single user.

    Skips bugs that don't belong to the user's company or are deleted.

    Args:
        ctx: ServiceContext with company_id and user_id.

    Returns:
        Count of bugs successfully assigned.
    """
    from services.assignment import assign_bug

    count = 0
    for bug_id in bug_ids:
        bug = db.get(Bug, bug_id)
        if bug and bug.company_id == ctx.company_id and not bug.is_deleted:
            try:
                assign_bug(db, bug, assigned_to_user_id, ctx)
                count += 1
            except (ValueError, Exception):
                pass  # Skip invalid assignments
    return count


def bulk_update_status(
    db: Session, bug_ids: list[UUID], new_status: BugStatus, ctx
) -> int:
    """
    Update status for multiple bugs.

    Uses the lifecycle state machine — invalid transitions are silently skipped.

    Returns:
        Count of bugs successfully updated.
    """
    from services.lifecycle import transition_bug

    count = 0
    for bug_id in bug_ids:
        bug = db.get(Bug, bug_id)
        if bug and bug.company_id == ctx.company_id and not bug.is_deleted:
            try:
                transition_bug(db, bug, new_status, ctx)
                count += 1
            except ValueError:
                pass  # Skip invalid transitions
    return count


def bulk_delete(db: Session, bug_ids: list[UUID], ctx) -> int:
    """
    Soft delete multiple bugs.

    Sets is_deleted=True on each bug (does NOT physically delete rows).

    Returns:
        Count of bugs successfully soft-deleted.
    """
    count = 0
    for bug_id in bug_ids:
        bug = db.get(Bug, bug_id)
        if bug and bug.company_id == ctx.company_id and not bug.is_deleted:
            bug.is_deleted = True
            count += 1

    db.commit()
    return count
