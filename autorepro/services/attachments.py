"""
AutoRepro Enterprise — Attachment Service (V2.0)

Manages file uploads on the local filesystem.
Path structure: {UPLOAD_DIR}/{company_id}/{bug_id}/{uuid}_{filename}

Validations:
  - File size must be < MAX_ATTACHMENT_SIZE_MB.
  - Filenames are prefixed with a UUID to prevent collisions.
"""

import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlmodel import Session

from db.models import User
from db.models_v2 import BugAttachment
from utils.config import MAX_ATTACHMENT_SIZE_MB, UPLOAD_DIR


def save_attachment(
    db: Session,
    bug_id: UUID,
    company_id: UUID,
    file: UploadFile,
    user: User,
) -> BugAttachment:
    """
    Save an uploaded file to the local filesystem and create a DB record.

    Path: {UPLOAD_DIR}/{company_id}/{bug_id}/{uuid}_{filename}

    Raises:
        ValueError: If the file exceeds MAX_ATTACHMENT_SIZE_MB.
    """
    # Validate file size
    file.file.seek(0, 2)  # Seek to end to get size
    size_bytes = file.file.tell()
    file.file.seek(0)  # Reset to beginning for reading

    max_bytes = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
    if size_bytes > max_bytes:
        raise ValueError(f"File too large (max {MAX_ATTACHMENT_SIZE_MB}MB)")

    # Create directory structure
    upload_path = Path(UPLOAD_DIR) / str(company_id) / str(bug_id)
    upload_path.mkdir(parents=True, exist_ok=True)

    # Generate safe filename with UUID prefix to prevent collisions
    unique_id = uuid4()
    safe_filename = f"{unique_id}_{file.filename}"
    filepath = upload_path / safe_filename

    # Write file to disk
    with open(filepath, "wb") as f:
        f.write(file.file.read())

    # Create DB record
    attachment = BugAttachment(
        bug_id=bug_id,
        company_id=company_id,
        uploaded_by_user_id=user.id,
        filename=file.filename or "untitled",
        filepath=str(filepath),
        size_bytes=size_bytes,
        mime_type=file.content_type or "application/octet-stream",
    )

    db.add(attachment)
    db.commit()
    db.refresh(attachment)

    return attachment


def delete_attachment(db: Session, attachment: BugAttachment) -> None:
    """
    Soft delete an attachment. The actual file remains on disk until
    the retention cleanup worker removes it.
    """
    attachment.is_deleted = True
    db.commit()
