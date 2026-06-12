"""
AutoRepro Enterprise — Full-Text Search Service (V2.0)

Uses Postgres tsvector + plainto_tsquery for full-text search on bug
title and description. Results are ranked by relevance using ts_rank().

Requires GIN index on bugs table (created via Alembic migration):
  CREATE INDEX ix_bugs_search ON bugs USING gin(
      to_tsvector('english', title || ' ' || description)
  );
"""

from typing import Optional
from uuid import UUID

from sqlmodel import Session, select, text

from db.models import Bug


def search_bugs(
    db: Session,
    company_id: UUID,
    query: str,
    filters: Optional[dict] = None,
) -> list[Bug]:
    """
    Full-text search on bug title + description using Postgres tsvector.

    Args:
        company_id: Tenant isolation — only search within this company.
        query: Free-text search query string.
        filters: Optional dict with keys: "status", "priority", "assigned_to".

    Returns:
        List of Bug records ordered by relevance (ts_rank DESC).
    """
    stmt = select(Bug).where(
        Bug.company_id == company_id,
        Bug.is_deleted == False,  # noqa: E712
        text(
            "to_tsvector('english', title || ' ' || coalesce(description, '')) "
            "@@ plainto_tsquery(:search_query)"
        ),
    )

    # Apply optional filters
    if filters:
        if "status" in filters:
            stmt = stmt.where(Bug.status == filters["status"])
        if "priority" in filters:
            stmt = stmt.where(Bug.priority == filters["priority"])
        if "assigned_to" in filters:
            stmt = stmt.where(Bug.assigned_to == filters["assigned_to"])

    # Order by relevance score (highest first)
    stmt = stmt.order_by(
        text(
            "ts_rank(to_tsvector('english', title || ' ' || coalesce(description, '')), "
            "plainto_tsquery(:search_query)) DESC"
        )
    ).params(search_query=query)

    bugs = db.exec(stmt).all()
    return bugs
