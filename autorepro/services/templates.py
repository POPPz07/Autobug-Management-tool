"""
AutoRepro Enterprise — Bug Template Service (V2.0)

Creates bugs from reusable templates with {{placeholder}} substitution.
Example template: "Login failed on {{browser}} when using {{credentials_type}}"
"""

from uuid import UUID

from sqlmodel import Session

from db.models import Bug, BugStatus, User
from db.models_v2 import BugTemplate


def create_bug_from_template(
    db: Session, template: BugTemplate, user: User, overrides: dict
) -> Bug:
    """
    Instantiate a new bug from a template with placeholder replacement.

    Args:
        template: The BugTemplate to use as a base.
        user: The user creating the bug (sets created_by, reported_by, company_id).
        overrides: Dict with optional keys:
            - "placeholders": {"browser": "Chrome", "auth_type": "OAuth"} — replaces {{key}}
            - "title": override the default template name
            - "target_url": REQUIRED — the URL to reproduce against
            - "priority": override default_priority
            - "severity": override default_severity
            - "environment": override default_environment

    Returns:
        The newly created Bug record.
    """
    # Start with the template's description
    description = template.description_template

    # Replace {{placeholder}} tokens with provided values
    if "placeholders" in overrides:
        for key, value in overrides["placeholders"].items():
            description = description.replace(f"{{{{{key}}}}}", value)

    # Create the bug with template defaults + any overrides
    bug = Bug(
        title=overrides.get("title", template.name),
        description=description,
        target_url=overrides["target_url"],
        status=BugStatus.CREATED,
        priority=overrides.get("priority", template.default_priority),
        severity=overrides.get("severity", template.default_severity),
        environment=overrides.get("environment", template.default_environment),
        company_id=user.company_id,
        created_by_user_id=user.id,
        reported_by=user.id,
        template_id=template.id,
    )

    db.add(bug)
    db.commit()
    db.refresh(bug)

    return bug
