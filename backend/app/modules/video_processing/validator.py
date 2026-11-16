"""Validation logic for uploaded video files."""

import os
from pathlib import Path
from typing import Optional, Set
from fastapi import HTTPException, status

# Initial set of supported container file extensions
SUPPORTED_EXTENSIONS: Set[str] = {".mp4", ".mov", ".mkv", ".webm"}

# Permitted MIME types for preliminary filtering
SUPPORTED_MIME_TYPES: Set[str] = {
    "video/mp4",
    "video/quicktime",
    "video/x-matroska",
    "video/mkv",
    "video/webm",
    "application/octet-stream",  # Often sent as fallback by some browsers
}


def validate_file_extension(filename: Optional[str]) -> str:
    """Validate and return normalized file extension.

    Raises:
        HTTPException(400): If filename is missing or empty.
        HTTPException(415): If extension is not supported.
    """
    if not filename or not filename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required and cannot be empty.",
        )

    # Securely extract extension
    _, ext = os.path.splitext(filename.strip().lower())

    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File has no extension.",
        )

    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file format '{ext}'. Supported formats: {supported}",
        )

    return ext


def validate_preliminary_mime_type(content_type: Optional[str]) -> None:
    """Perform preliminary MIME type check if supplied by client.

    Client MIME types cannot be trusted, but non-video types (e.g. image/png, text/plain)
    can be rejected upfront.

    Raises:
        HTTPException(415): If content_type clearly does not correspond to supported video.
    """
    if not content_type:
        return

    clean_mime = content_type.strip().lower().split(";")[0]
    if clean_mime not in SUPPORTED_MIME_TYPES and not clean_mime.startswith("video/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type '{clean_mime}'.",
        )
