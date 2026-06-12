"""
AutoRepro Enterprise — Auth Utilities Service (V2.0)

Handles password reset tokens and email verification tokens.
Tokens are stored as SHA256 hashes in the DB; raw tokens are sent via email.

Security:
  - create_password_reset_token() returns None silently if email not found (prevents enumeration).
  - Tokens expire after 1 hour.
  - Tokens are single-use (marked with used_at timestamp).
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
import hashlib
import secrets

from sqlmodel import Session, select

from db.models import User
from db.models_v2 import PasswordResetToken


def create_password_reset_token(db: Session, email: str) -> Optional[str]:
    """
    Generate a password reset token for the given email.

    Returns:
        str: The raw (unhashed) token to send via email.
        None: If the email doesn't exist (silent — never reveal this to the caller).

    Side effects:
        - Inserts a PasswordResetToken row with SHA256 hash and 1-hour expiry.
    """
    stmt = select(User).where(User.email == email, User.is_deleted == False)
    user = db.exec(stmt).first()

    if not user:
        # Don't reveal whether the email exists — return None silently
        return None

    # Generate cryptographically secure random token
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Persist hashed token with 1-hour TTL
    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(reset_token)
    db.commit()

    return token


def verify_password_reset_token(db: Session, token: str) -> Optional[User]:
    """
    Validate a reset token and return the associated user.

    Returns:
        User: If token is valid, unused, and not expired.
        None: Otherwise.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    stmt = select(PasswordResetToken).where(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.used_at == None,  # noqa: E711 — SQLAlchemy requires == None
        PasswordResetToken.expires_at > datetime.now(timezone.utc),
    )
    reset_token = db.exec(stmt).first()

    if not reset_token:
        return None

    user = db.get(User, reset_token.user_id)
    return user


def mark_token_used(db: Session, token: str) -> None:
    """
    Mark a reset token as used so it cannot be reused.

    Called immediately after the password has been successfully changed.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    stmt = select(PasswordResetToken).where(
        PasswordResetToken.token_hash == token_hash,
    )
    reset_token = db.exec(stmt).first()

    if reset_token:
        reset_token.used_at = datetime.now(timezone.utc)
        db.commit()


def generate_email_verification_token() -> str:
    """
    Generate a random email verification token.

    The raw token is stored on User.email_verification_token and sent via email.
    When the user clicks the link, we match it directly (no hashing needed for
    email verification since it's lower-risk than password reset).
    """
    return secrets.token_urlsafe(32)
